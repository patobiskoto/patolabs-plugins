"""Closed, read-only GitHub check-runs delivery-proof pilot.

The pilot is intentionally bound to one public repository, one proof source, and
one GET-only adapter. It cannot execute work or carry credentials. Receipts are
derived from adapter observations; caller-built receipt mappings are never an
append authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


CONTRACT_SCHEMA = "foundry-delivery-contract.v1"
PROOF_SCHEMA = "foundry-delivery-proof.v1"
RECEIPT_SCHEMA = "foundry-delivery-receipt.v1"
PILOT_PROJECT = "patobiskoto/patolabs-plugins"
PILOT_CONTRACT_VERSION = "github-check-runs-v1"
PILOT_SOURCE_ID = "github_check_runs"
PILOT_ADAPTER = "github_check_runs_readonly"
PILOT_ADAPTER_VERSION = "v1"
PILOT_PROVENANCE = "github_rest_check_runs"
PILOT_REQUIREMENT_ID = "github_checks"
PILOT_REQUIRED_FACT = "check_runs_success"

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_GITHUB_PROJECT = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,38})/[a-z0-9](?:[a-z0-9._-]{0,99})\Z",
)
_STATES = frozenset(("success", "pending", "missing", "unavailable", "unsupported"))
_OUTCOME_PRECEDENCE = (
    "provenance-mismatch",
    "cross-project",
    "wrong-sha",
    "unsupported-schema",
    "inaccessible",
    "pending",
    "missing-proof",
    "unavailable",
    "failed-proof",
)
_OUTCOMES = frozenset((*_OUTCOME_PRECEDENCE, "success"))
_VERDICTS = frozenset((*_OUTCOME_PRECEDENCE, "verified"))
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_GITHUB_PENDING_STATUSES = frozenset(("queued", "in_progress", "waiting", "requested", "pending"))
_GITHUB_PASSING_CONCLUSIONS = frozenset(("success",))
_GITHUB_NONPROVING_CONCLUSIONS = frozenset(("neutral", "skipped"))
_GITHUB_FAILING_CONCLUSIONS = frozenset(
    ("action_required", "cancelled", "failure", "stale", "startup_failure", "timed_out"),
)
_GITHUB_API_ROOT = "https://api.github.com"
_GITHUB_RESPONSE_LIMIT = 1_048_576
_GITHUB_TIMEOUT_SECONDS = 10.0
MAX_LOCAL_RECEIPTS = 128


class DeliveryContractError(ValueError):
    """The closed pilot contract, proof, or receipt is invalid."""


class ProofSourceUnavailable(Exception):
    """The public read-only source could not be observed."""


class ProofSourceUnsupported(Exception):
    """The source responded, but not in the pilot's supported shape."""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DeliveryContractError(f"{field} must be a lowercase identifier")
    return value


def _project(value: object, field: str = "project") -> str:
    if not isinstance(value, str) or not _GITHUB_PROJECT.fullmatch(value):
        raise DeliveryContractError(f"{field} must be a canonical GitHub owner/repository")
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


def _utc_now() -> str:
    """Capture canonical time internally; tests may replace this private clock."""
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _closed(mapping: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise DeliveryContractError(f"{label} has unsupported fields")


def _supported_literal(value: object, expected: str, field: str) -> str:
    if value != expected:
        raise DeliveryContractError(f"{field} is unsupported by the delivery pilot")
    return expected


def _pilot_contract_value() -> dict[str, Any]:
    return {
        "schema": CONTRACT_SCHEMA,
        "version": PILOT_CONTRACT_VERSION,
        "project": PILOT_PROJECT,
        "sources": [
            {
                "id": PILOT_SOURCE_ID,
                "adapter": PILOT_ADAPTER,
                "adapter_version": PILOT_ADAPTER_VERSION,
                "provenance": PILOT_PROVENANCE,
            },
        ],
        "requirements": [
            {
                "id": PILOT_REQUIREMENT_ID,
                "source": PILOT_SOURCE_ID,
                "required_facts": [PILOT_REQUIRED_FACT],
            },
        ],
    }


def github_check_runs_pilot_contract() -> dict[str, Any]:
    """Return a detached copy of the one supported project-native contract."""
    return validate_contract(_pilot_contract_value())


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact, declarative GitHub check-runs pilot declaration."""
    if not isinstance(contract, Mapping):
        raise DeliveryContractError("contract must be an object")
    _closed(
        contract,
        frozenset(("schema", "version", "project", "sources", "requirements")),
        "contract",
    )
    _supported_literal(contract.get("schema"), CONTRACT_SCHEMA, "contract schema")
    version = _supported_literal(
        contract.get("version"),
        PILOT_CONTRACT_VERSION,
        "contract version",
    )
    project = _project(contract.get("project"), "contract project")
    _supported_literal(project, PILOT_PROJECT, "contract project")

    sources = contract.get("sources")
    if not isinstance(sources, list) or len(sources) != 1:
        raise DeliveryContractError("contract requires exactly one pilot source")
    source = sources[0]
    if not isinstance(source, Mapping):
        raise DeliveryContractError("source must be an object")
    _closed(source, frozenset(("id", "adapter", "adapter_version", "provenance")), "source")
    canonical_source = {
        "id": _supported_literal(source.get("id"), PILOT_SOURCE_ID, "source id"),
        "adapter": _supported_literal(source.get("adapter"), PILOT_ADAPTER, "source adapter"),
        "adapter_version": _supported_literal(
            source.get("adapter_version"),
            PILOT_ADAPTER_VERSION,
            "source adapter version",
        ),
        "provenance": _supported_literal(
            source.get("provenance"),
            PILOT_PROVENANCE,
            "source provenance",
        ),
    }

    requirements = contract.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != 1:
        raise DeliveryContractError("contract requires exactly one pilot requirement")
    requirement = requirements[0]
    if not isinstance(requirement, Mapping):
        raise DeliveryContractError("requirement must be an object")
    _closed(requirement, frozenset(("id", "source", "required_facts")), "requirement")
    requirement_id = _supported_literal(
        requirement.get("id"),
        PILOT_REQUIREMENT_ID,
        "requirement id",
    )
    requirement_source = _supported_literal(
        requirement.get("source"),
        PILOT_SOURCE_ID,
        "requirement source",
    )
    facts = requirement.get("required_facts")
    if facts != [PILOT_REQUIRED_FACT]:
        raise DeliveryContractError("requirement facts are unsupported by the delivery pilot")

    return {
        "schema": CONTRACT_SCHEMA,
        "version": version,
        "project": project,
        "sources": [canonical_source],
        "requirements": [
            {
                "id": requirement_id,
                "source": requirement_source,
                "required_facts": [PILOT_REQUIRED_FACT],
            },
        ],
    }


def contract_digest(contract: Mapping[str, Any]) -> str:
    """Return the SHA-256 of the validated, canonical pilot contract."""
    return _digest(validate_contract(contract))


@dataclass(frozen=True)
class Proof:
    source: str
    project: str
    sha: str
    state: str
    provenance: str
    facts: dict[str, object]


class _GitHubCheckRunsTransport(Protocol):
    def get_check_runs(self, *, project: str, sha: str) -> object: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class _GitHubPublicCheckRunsTransport:
    """Fixed-host, unauthenticated, GET-only transport for the public pilot."""

    def get_check_runs(self, *, project: str, sha: str) -> object:
        project = _project(project)
        sha = _sha(sha)
        if project != PILOT_PROJECT:
            raise DeliveryContractError("GitHub adapter project is outside the pilot")
        request = urllib.request.Request(
            f"{_GITHUB_API_ROOT}/repos/{project}/commits/{sha}/check-runs?per_page=100",
            method="GET",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "foundry-delivery-proof-pilot",
            },
        )
        # Disable environment-derived proxies as well as redirects so the pilot
        # cannot inherit proxy credentials or leave its single declared origin.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        try:
            with opener.open(request, timeout=_GITHUB_TIMEOUT_SECONDS) as response:
                raw = response.read(_GITHUB_RESPONSE_LIMIT + 1)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise ProofSourceUnavailable("GitHub check-runs source unavailable") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProofSourceUnavailable("GitHub check-runs source unavailable") from None
        if len(raw) > _GITHUB_RESPONSE_LIMIT:
            raise ProofSourceUnsupported("GitHub check-runs response is unsupported")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ProofSourceUnsupported("GitHub check-runs response is unsupported") from None


class GitHubCheckRunsAdapter:
    """Concrete project-native proof adapter with no credential or mutation surface."""

    source_id = PILOT_SOURCE_ID
    adapter = PILOT_ADAPTER
    adapter_version = PILOT_ADAPTER_VERSION
    provenance = PILOT_PROVENANCE

    def __init__(self, *, _transport: _GitHubCheckRunsTransport | None = None):
        # ``_transport`` is only a private test seam. Production uses the
        # fixed-host, unauthenticated GET transport above.
        self._transport = (
            _GitHubPublicCheckRunsTransport() if _transport is None else _transport
        )

    @staticmethod
    def _proof(
        *,
        project: str,
        sha: str,
        state: str,
        check_runs_success: bool | None = None,
    ) -> dict[str, object]:
        facts: dict[str, object] = {}
        if check_runs_success is not None:
            facts[PILOT_REQUIRED_FACT] = check_runs_success
        return {
            "schema": PROOF_SCHEMA,
            "source": PILOT_SOURCE_ID,
            "project": project,
            "sha": sha,
            "state": state,
            "provenance": PILOT_PROVENANCE,
            "facts": facts,
        }

    def read_proof(self, *, project: str, sha: str) -> Mapping[str, object]:
        project = _project(project)
        sha = _sha(sha)
        if project != PILOT_PROJECT:
            raise DeliveryContractError("GitHub adapter project is outside the pilot")
        try:
            payload = self._transport.get_check_runs(project=project, sha=sha)
        except ProofSourceUnavailable:
            raise
        except ProofSourceUnsupported:
            return self._proof(project=project, sha=sha, state="unsupported")
        return self._map_payload(payload, project=project, sha=sha)

    def _map_payload(self, payload: object, *, project: str, sha: str) -> dict[str, object]:
        if not isinstance(payload, Mapping):
            return self._proof(project=project, sha=sha, state="unsupported")
        total_count = payload.get("total_count")
        runs = payload.get("check_runs")
        if type(total_count) is not int or total_count < 0 or not isinstance(runs, list):
            return self._proof(project=project, sha=sha, state="unsupported")
        # ``per_page=100`` is fixed above. A mismatch means the response is
        # partial or internally incoherent, so this narrow pilot refuses it.
        if total_count != len(runs):
            return self._proof(project=project, sha=sha, state="unsupported")
        if not runs:
            return self._proof(project=project, sha=sha, state="missing")

        conclusions: list[str] = []
        pending = False
        for run in runs:
            if not isinstance(run, Mapping):
                return self._proof(project=project, sha=sha, state="unsupported")
            observed_sha = run.get("head_sha")
            if not isinstance(observed_sha, str) or not _SHA.fullmatch(observed_sha):
                return self._proof(project=project, sha=sha, state="unsupported")
            if observed_sha != sha:
                # Preserve only the bounded SHA necessary for the evaluator to
                # classify the observation as wrong-SHA.
                return self._proof(project=project, sha=observed_sha, state="success")
            status = run.get("status")
            conclusion = run.get("conclusion")
            if status in _GITHUB_PENDING_STATUSES:
                if conclusion is not None:
                    return self._proof(project=project, sha=sha, state="unsupported")
                pending = True
                continue
            if status != "completed" or not isinstance(conclusion, str):
                return self._proof(project=project, sha=sha, state="unsupported")
            supported = (
                _GITHUB_PASSING_CONCLUSIONS
                | _GITHUB_NONPROVING_CONCLUSIONS
                | _GITHUB_FAILING_CONCLUSIONS
            )
            if conclusion not in supported:
                return self._proof(project=project, sha=sha, state="unsupported")
            conclusions.append(conclusion)

        if pending:
            return self._proof(project=project, sha=sha, state="pending")
        if any(item in _GITHUB_FAILING_CONCLUSIONS for item in conclusions):
            return self._proof(
                project=project,
                sha=sha,
                state="success",
                check_runs_success=False,
            )
        if any(item in _GITHUB_PASSING_CONCLUSIONS for item in conclusions):
            return self._proof(
                project=project,
                sha=sha,
                state="success",
                check_runs_success=True,
            )
        # neutral/skipped alone is not positive evidence under ADR-0002.
        return self._proof(project=project, sha=sha, state="missing")


def _proof(value: object, source: str) -> Proof:
    if not isinstance(value, Mapping):
        raise DeliveryContractError("proof must be an object")
    _closed(
        value,
        frozenset(("schema", "source", "project", "sha", "state", "provenance", "facts")),
        "proof",
    )
    if value.get("schema") != PROOF_SCHEMA:
        raise DeliveryContractError("unsupported proof schema")
    if value.get("source") != source or source != PILOT_SOURCE_ID:
        raise DeliveryContractError("proof source does not match the pilot source")
    facts = value.get("facts", {})
    if not isinstance(facts, Mapping):
        raise DeliveryContractError("proof facts must be an object")
    canonical_facts = {}
    for key, fact in sorted(facts.items()):
        key = _identifier(key, "fact")
        if fact is not None and not isinstance(fact, bool):
            raise DeliveryContractError("proof facts must be boolean or null")
        canonical_facts[key] = fact
    state = value.get("state")
    if not isinstance(state, str) or state not in _STATES:
        raise DeliveryContractError("proof state is unsupported")
    return Proof(
        source,
        _project(value.get("project"), "proof project"),
        _sha(value.get("sha")),
        state,
        _identifier(value.get("provenance"), "proof provenance"),
        canonical_facts,
    )


def _source_adapter(adapter: object, source: Mapping[str, str]) -> GitHubCheckRunsAdapter:
    """Bind the concrete adapter to the sole supported declaration."""
    if type(adapter) is not GitHubCheckRunsAdapter:
        raise DeliveryContractError("the delivery pilot requires GitHubCheckRunsAdapter")
    for field, declared_field in (
        ("source_id", "id"),
        ("adapter", "adapter"),
        ("adapter_version", "adapter_version"),
        ("provenance", "provenance"),
    ):
        if getattr(adapter, field, None) != source[declared_field]:
            raise DeliveryContractError("adapter does not match the pilot source")
    return adapter


def _source_failure_proof(
    canonical: Mapping[str, Any],
    *,
    sha: str,
    state: str,
) -> dict[str, object]:
    source = canonical["sources"][0]
    return {
        "schema": PROOF_SCHEMA,
        "source": source["id"],
        "project": canonical["project"],
        "sha": sha,
        "state": state,
        "provenance": source["provenance"],
        "facts": {},
    }


def read_delivery_proof(
    contract: Mapping[str, Any],
    *,
    sha: str,
    adapter: GitHubCheckRunsAdapter,
) -> dict[str, object]:
    """Read the exact pilot source through its concrete GET-only adapter."""
    canonical = validate_contract(contract)
    sha = _sha(sha)
    source = canonical["sources"][0]
    bound = _source_adapter(adapter, source)
    try:
        proof = bound.read_proof(project=canonical["project"], sha=sha)
    except ProofSourceUnavailable:
        proof = _source_failure_proof(canonical, sha=sha, state="unavailable")
    except ProofSourceUnsupported:
        proof = _source_failure_proof(canonical, sha=sha, state="unsupported")
    if not isinstance(proof, Mapping):
        raise DeliveryContractError("adapter proof must be an object")
    return dict(proof)


def delivery_receipt_from_adapter(
    contract: Mapping[str, Any],
    *,
    sha: str,
    adapter: GitHubCheckRunsAdapter,
    observation_id: str,
) -> dict[str, Any]:
    """Observe the pilot source and materialize a canonical exact-SHA receipt."""
    proof = read_delivery_proof(contract, sha=sha, adapter=adapter)
    return _delivery_receipt_from_observations(
        contract,
        sha=sha,
        observations={PILOT_SOURCE_ID: proof},
        observation_id=observation_id,
        observed_at=_utc_now(),
    )


def _delivery_receipt_from_observations(
    contract: Mapping[str, Any],
    *,
    sha: str,
    observations: Mapping[str, object],
    observation_id: str,
    observed_at: str,
) -> dict[str, Any]:
    """Pure evaluator retained only as a private deterministic test seam."""
    canonical = validate_contract(contract)
    project = canonical["project"]
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
            "requirement": requirement["id"],
            "source": source,
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
                row["outcome"] = (
                    "unsupported-schema"
                    if str(exc) == "unsupported proof schema"
                    else "missing-proof"
                )
            else:
                if proof.provenance != sources[source]["provenance"]:
                    row["outcome"] = "provenance-mismatch"
                else:
                    row["provenance"] = sources[source]["provenance"]
                    if proof.project != project:
                        row["outcome"] = "cross-project"
                    elif proof.sha != sha:
                        row["outcome"] = "wrong-sha"
                    elif proof.state == "unsupported":
                        row["outcome"] = "unsupported-schema"
                    elif proof.state == "pending":
                        row["outcome"] = "pending"
                    elif proof.state == "unavailable":
                        row["outcome"] = "inaccessible"
                    elif proof.state == "missing":
                        row["outcome"] = "missing-proof"
                    elif any(
                        proof.facts.get(fact) is None
                        for fact in requirement["required_facts"]
                    ):
                        row["outcome"] = "unavailable"
                    elif any(
                        proof.facts[fact] is False
                        for fact in requirement["required_facts"]
                    ):
                        row["outcome"] = "failed-proof"
                    else:
                        row["outcome"] = "success"
        outcomes.append(row["outcome"])
        proof_rows.append(row)
    verdict = _verdict_from_outcomes(outcomes)
    return {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": observation_id,
        "project": project,
        "sha": sha,
        "contract": {
            "schema": CONTRACT_SCHEMA,
            "version": canonical["version"],
            "digest": _digest(canonical),
        },
        "observed_at": observed_at,
        "verdict": verdict,
        "proofs": proof_rows,
    }


def _verdict_from_outcomes(outcomes: list[str]) -> str:
    if not outcomes:
        raise DeliveryContractError("receipt proofs must be non-empty")
    return next((state for state in _OUTCOME_PRECEDENCE if state in outcomes), "verified")


class DeliveryReceiptJournal:
    """Bounded local log that appends only freshly observed pilot receipts."""

    def __init__(self, path: Path, *, max_receipts: int = MAX_LOCAL_RECEIPTS):
        if not isinstance(path, Path):
            raise DeliveryContractError("journal path must be a Path")
        if not isinstance(max_receipts, int) or not 1 <= max_receipts <= MAX_LOCAL_RECEIPTS:
            raise DeliveryContractError("journal max_receipts is out of bounds")
        self.path = path
        self.max_receipts = max_receipts

    def append(
        self,
        *,
        contract: Mapping[str, Any],
        sha: str,
        adapter: GitHubCheckRunsAdapter,
        observation_id: str,
    ) -> dict[str, Any]:
        """Observe, derive, validate, and append; raw receipt input is impossible."""
        derived = delivery_receipt_from_adapter(
            contract,
            sha=sha,
            adapter=adapter,
            observation_id=observation_id,
        )
        canonical = self._validated_receipt(derived)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="ascii") as handle:
            self._lock(handle)
            try:
                handle.seek(0)
                try:
                    rows = [
                        self._validated_receipt(json.loads(line))
                        for line in handle
                        if line.strip()
                    ]
                except (json.JSONDecodeError, UnicodeError) as exc:
                    raise DeliveryContractError("local receipt journal is malformed") from exc
                if len(rows) >= self.max_receipts:
                    raise DeliveryContractError("local receipt journal is full")
                if any(row["receipt_id"] == canonical["receipt_id"] for row in rows):
                    raise DeliveryContractError("receipt_id already exists in local journal")
                handle.seek(0, os.SEEK_END)
                handle.write(_canonical(canonical) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                self._unlock(handle)
        return json.loads(_canonical(canonical))

    def receipts(self) -> tuple[dict[str, Any], ...]:
        """Read and revalidate every detached journal entry fail-closed."""
        if not self.path.exists():
            return ()
        try:
            with self.path.open(encoding="ascii") as handle:
                return tuple(
                    self._validated_receipt(json.loads(line))
                    for line in handle
                    if line.strip()
                )
        except (json.JSONDecodeError, UnicodeError) as exc:
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
        _closed(
            value,
            frozenset(
                (
                    "schema",
                    "receipt_id",
                    "project",
                    "sha",
                    "contract",
                    "observed_at",
                    "verdict",
                    "proofs",
                ),
            ),
            "receipt",
        )
        if value.get("schema") != RECEIPT_SCHEMA:
            raise DeliveryContractError("unsupported receipt schema")
        _identifier(value.get("receipt_id"), "receipt id")
        if _project(value.get("project"), "receipt project") != PILOT_PROJECT:
            raise DeliveryContractError("receipt project is outside the pilot")
        _sha(value.get("sha"), "receipt sha")
        _timestamp(value.get("observed_at"))
        verdict = value.get("verdict")
        if not isinstance(verdict, str) or verdict not in _VERDICTS:
            raise DeliveryContractError("receipt verdict is unsupported")

        contract = value.get("contract")
        expected_contract = github_check_runs_pilot_contract()
        expected_reference = {
            "schema": CONTRACT_SCHEMA,
            "version": PILOT_CONTRACT_VERSION,
            "digest": _digest(expected_contract),
        }
        if not isinstance(contract, Mapping) or dict(contract) != expected_reference:
            raise DeliveryContractError("receipt contract is not the pilot contract")
        _digest_value(contract.get("digest"), "receipt contract digest")

        proofs = value.get("proofs")
        if not isinstance(proofs, list) or len(proofs) != 1:
            raise DeliveryContractError("receipt proofs must contain the one pilot requirement")
        proof = proofs[0]
        if not isinstance(proof, Mapping):
            raise DeliveryContractError("receipt proof must be an object")
        _closed(
            proof,
            frozenset(
                (
                    "requirement",
                    "source",
                    "adapter",
                    "adapter_version",
                    "source_provenance",
                    "provenance",
                    "outcome",
                ),
            ),
            "receipt proof",
        )
        expected_identity = {
            "requirement": PILOT_REQUIREMENT_ID,
            "source": PILOT_SOURCE_ID,
            "adapter": PILOT_ADAPTER,
            "adapter_version": PILOT_ADAPTER_VERSION,
            "source_provenance": PILOT_PROVENANCE,
        }
        if any(proof.get(field) != expected for field, expected in expected_identity.items()):
            raise DeliveryContractError("receipt proof identity is outside the pilot")
        outcome = proof.get("outcome")
        if not isinstance(outcome, str) or outcome not in _OUTCOMES:
            raise DeliveryContractError("receipt proof outcome is unsupported")
        no_observed_provenance = {
            "missing-proof",
            "unsupported-schema",
            "provenance-mismatch",
        }
        if outcome in no_observed_provenance:
            if "provenance" in proof:
                raise DeliveryContractError("receipt proof provenance is incoherent")
        elif proof.get("provenance") != PILOT_PROVENANCE:
            raise DeliveryContractError("receipt proof provenance is incoherent")
        if verdict != _verdict_from_outcomes([outcome]):
            raise DeliveryContractError("receipt verdict does not match proof outcomes")
        try:
            return json.loads(_canonical(value))
        except (TypeError, ValueError) as exc:
            raise DeliveryContractError("receipt is not canonical JSON data") from exc


def claude_delivery_receipt(
    contract: Mapping[str, Any],
    *,
    sha: str,
    adapter: GitHubCheckRunsAdapter,
    observation_id: str,
) -> dict[str, Any]:
    """Claude facade over the shared contract-bound adapter implementation."""
    return delivery_receipt_from_adapter(
        contract,
        sha=sha,
        adapter=adapter,
        observation_id=observation_id,
    )


def codex_delivery_receipt(
    contract: Mapping[str, Any],
    *,
    sha: str,
    adapter: GitHubCheckRunsAdapter,
    observation_id: str,
) -> dict[str, Any]:
    """Codex facade over the shared contract-bound adapter implementation."""
    return delivery_receipt_from_adapter(
        contract,
        sha=sha,
        adapter=adapter,
        observation_id=observation_id,
    )
