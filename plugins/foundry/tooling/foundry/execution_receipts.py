"""Passive execution receipts bound to one exact runtime attempt.

Only mechanical Foundry boundaries call this module.  The builders accept normalized
CodeHost, gate, review-proof and tracker results; they never inspect prompts, terminal
output or agent prose.  Persistence is an append-only enrichment seam for the passive
FOUNDRY-88 publisher and has no authority over the operation that produced the fact.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Iterator, Mapping
from urllib.parse import urlparse

from foundry import registry
from foundry.models import EpicClosureOutcome, PullRequest


SCHEMA_VERSION = "foundry-execution-receipts.v2"
SOURCE_ADAPTER_VERSION = "foundry-structured-source.v1"
EPIC_CLOSURE_COORDINATE_VERSION = "devhub-foundry-epic-closure.v1"
ATTEMPT_ID_ENV = "FOUNDRY_ATTEMPT_ID"
RECEIPT_DIRECTORY_ENV = "FOUNDRY_EXECUTION_RECEIPTS_DIR"
RECEIPT_KINDS = ("pr", "review", "ci", "merge", "epic_closure")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_ISSUE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-\d+$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PROVIDERS = frozenset({"github", "forgejo"})
_SOURCE_OPERATIONS = {
    "pr": frozenset({
        "codehost.open_pr", "codehost.get_pr", "codehost.update_pr",
    }),
    "review": frozenset({"acceptance_proof.valid_for_merge"}),
    "ci": frozenset({"ci_gate.evaluate"}),
    "merge": frozenset({"codehost.merge_pr", "codehost.get_pr"}),
    "epic_closure": frozenset({"tracker.close_epic"}),
}
_SOURCE_PROVENANCE = {
    "pr": "codehost",
    "review": "review-proof",
    "ci": "ci-gate",
    "merge": "codehost",
    "epic_closure": "tracker",
}
_REJECTION_REASONS = frozenset({
    "divergent-observation", "divergent-source", "source-owned",
})
_MAX_OBSERVED_AT = 4_102_444_800_000  # 2100-01-01T00:00:00Z, epoch ms
_MAX_BINDINGS = 256
_MAX_BYTES = 2 * 1024 * 1024


class ReceiptStoreError(RuntimeError):
    """A receipt was ambiguous, divergent, malformed or could not be persisted."""


@dataclass(frozen=True)
class ReceiptObservation:
    """One bounded structured-source observation, separate from its F88 facts."""

    kind: str
    source: Mapping[str, Any]
    receipt: Mapping[str, Any] | None


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _require_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ReceiptStoreError(f"invalid_{label}")
    return value


def _require_issue(value: object) -> str:
    if not isinstance(value, str) or _ISSUE_ID.fullmatch(value) is None:
        raise ReceiptStoreError("invalid_issue_id")
    return value


def _require_repository(value: object) -> str:
    if not isinstance(value, str) or _REPOSITORY.fullmatch(value) is None:
        raise ReceiptStoreError("invalid_repository")
    return value


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ReceiptStoreError(f"invalid_{label}")
    return value


def _optional_sha(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_sha(value, label)


def _require_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ReceiptStoreError("invalid_url")
    try:
        parsed = urlparse(value)
        invalid = (
            parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None or parsed.password is not None
            or parsed.params or parsed.query or parsed.fragment
            or any(ord(character) < 0x21 or ord(character) == 0x7f
                   for character in value)
        )
    except ValueError:
        invalid = True
    if invalid:
        # Query, fragment, params and userinfo are all unnecessary for the
        # normalized CodeHost links we persist, and each can carry credentials.
        # Reject instead of guessing which values are sensitive.
        raise ReceiptStoreError("invalid_url")
    return value


def _optional_url(value: object) -> str | None:
    if value is None:
        return None
    return _require_url(value)


def _observation_time(value: object | None) -> int:
    observed_at = time.time_ns() // 1_000_000 if value is None else value
    return _require_observed_at(observed_at)


def _require_observed_at(value: object) -> int:
    observed_at = value
    if (type(observed_at) is not int
            or not 0 <= observed_at <= _MAX_OBSERVED_AT):
        raise ReceiptStoreError("invalid_observed_at")
    return observed_at


def _require_provider(value: object) -> str:
    if value not in _PROVIDERS:
        raise ReceiptStoreError("unsupported_codehost")
    return str(value)


def _source(
    kind: str, operation: str, adapter: str, identity: Mapping[str, Any],
    observed_at: int | None,
) -> dict[str, Any]:
    source = {
        "operation": operation,
        "provenance": _SOURCE_PROVENANCE[kind],
        "adapter": _require_identifier(adapter, "source_adapter"),
        "adapter_version": SOURCE_ADAPTER_VERSION,
        "identity": dict(identity),
        "observed_at": _observation_time(observed_at),
    }
    _validate_source(kind, source)
    return source


def _observation(
    kind: str, source: Mapping[str, Any], receipt: Mapping[str, Any] | None,
) -> ReceiptObservation:
    return ReceiptObservation(
        kind=kind, source=dict(source),
        receipt=None if receipt is None else dict(receipt),
    )


def _provider_receipt_base(provider: object, repository: object, url: object) -> dict:
    return {
        "provider": _require_provider(provider),
        "repository": _require_repository(repository),
        "url": _optional_url(url),
    }


def _pr_state(pr: PullRequest) -> str:
    if pr.merged:
        return "merged"
    if pr.state not in {"open", "closed"}:
        raise ReceiptStoreError("invalid_pr_state")
    return pr.state


def pr_receipt(
    provider: str, repository: str, pr: PullRequest, *, operation: str,
    observed_at: int | None = None,
) -> ReceiptObservation:
    """Project one structured CodeHost PR response without local fallbacks."""
    if not isinstance(pr, PullRequest) or type(pr.number) is not int or pr.number < 1:
        raise ReceiptStoreError("invalid_pr")
    normalized_provider = _require_provider(provider)
    normalized_repository = _require_repository(repository)
    receipt = {
        **_provider_receipt_base(normalized_provider, normalized_repository, pr.url),
        "number": pr.number,
        "state": _pr_state(pr),
        "head_sha": _optional_sha(pr.sha, "head_sha"),
        "base_sha": _optional_sha(pr.base_sha, "base_sha"),
    }
    source = _source(
        "pr", operation, normalized_provider, {
            "provider": normalized_provider,
            "repository": normalized_repository,
            "number": pr.number,
        }, observed_at,
    )
    return _observation("pr", source, receipt)


def review_receipt(
    provider: str, repository: str, pr: PullRequest, proof: Mapping[str, Any], *,
    observed_at: int | None = None,
) -> ReceiptObservation:
    """Project a mergeable Foundry review proof on the exact structured PR."""
    if not isinstance(pr, PullRequest) or type(pr.number) is not int or pr.number < 1:
        raise ReceiptStoreError("invalid_review_pr")
    if not isinstance(proof, Mapping) or set(proof) != {
        "schema_version", "proof_id", "issue", "review", "coordinates", "quality",
    }:
        raise ReceiptStoreError("incomplete_review_proof")
    proof_id = proof.get("proof_id")
    coordinates = proof.get("coordinates")
    issue = proof.get("issue")
    review = proof.get("review")
    criteria = issue.get("criteria") if isinstance(issue, Mapping) else None
    if (proof.get("schema_version") != 1 or not isinstance(proof_id, str)
            or _DIGEST.fullmatch(proof_id) is None
            or proof.get("quality") != "mergeable"
            or not isinstance(issue, Mapping)
            or set(issue) != {"id", "ac_digest", "criteria"}
            or _ISSUE_ID.fullmatch(issue.get("id", "")) is None
            or not isinstance(issue.get("ac_digest"), str)
            or _DIGEST.fullmatch(issue["ac_digest"]) is None
            or not isinstance(review, Mapping)
            or set(review) != {"role", "generation", "claim_digest"}
            or review.get("role") != "reviewer"
            or type(review.get("generation")) is not int or review["generation"] < 1
            or not isinstance(review.get("claim_digest"), str)
            or _DIGEST.fullmatch(review["claim_digest"]) is None
            or not isinstance(coordinates, Mapping)
            or set(coordinates) != {"head", "diff_hash", "base"}
            or not isinstance(criteria, list) or not criteria
            or any(not isinstance(item, Mapping)
                   or set(item) != {"id", "digest", "verdict"}
                   or item.get("id") != f"ac-{index}"
                   or not isinstance(item.get("digest"), str)
                   or _DIGEST.fullmatch(item["digest"]) is None
                   or item.get("verdict") != "pass"
                   for index, item in enumerate(criteria, start=1))
            or _digest({key: value for key, value in proof.items() if key != "proof_id"})
            != proof_id
    ):
        raise ReceiptStoreError("incomplete_review_proof")
    head = _require_sha(coordinates.get("head"), "review_head_sha")
    if pr.sha != head:
        raise ReceiptStoreError("divergent_review_head")
    _require_sha(coordinates.get("base"), "review_base_sha")
    if pr.base_sha != coordinates["base"]:
        raise ReceiptStoreError("divergent_review_base")
    if not isinstance(coordinates.get("diff_hash"), str) or _DIGEST.fullmatch(
        coordinates["diff_hash"],
    ) is None:
        raise ReceiptStoreError("invalid_review_digest")
    normalized_provider = _require_provider(provider)
    normalized_repository = _require_repository(repository)
    receipt = {
        **_provider_receipt_base(normalized_provider, normalized_repository, pr.url),
        "pr_number": pr.number,
        "verdict": "approved",
        "review_id": proof_id,
        "reviewer": None,
        "head_sha": head,
    }
    source = _source(
        "review", "acceptance_proof.valid_for_merge", "foundry-review-proof", {
            "provider": normalized_provider,
            "repository": normalized_repository,
            "pr_number": pr.number,
            "review_id": proof_id,
        }, observed_at,
    )
    return _observation("review", source, receipt)


def ci_receipt(
    provider: str, repository: str, head_sha: str, gate: Mapping[str, Any], *,
    observed_at: int | None = None,
) -> ReceiptObservation:
    """Project the deterministic CI-gate verdict; no-CI remains unavailable."""
    if not isinstance(gate, Mapping) or not {
        "passed", "waived", "pending", "failing", "total",
    }.issubset(gate):
        raise ReceiptStoreError("incomplete_ci_gate")
    if (type(gate["passed"]) is not bool or type(gate["waived"]) is not bool
            or type(gate["total"]) is not int or gate["total"] < 0
            or not isinstance(gate["pending"], list)
            or not isinstance(gate["failing"], list)
            or any(not isinstance(item, str) for item in gate["pending"] + gate["failing"])):
        raise ReceiptStoreError("invalid_ci_gate")
    head = _require_sha(head_sha, "ci_head_sha")
    normalized_provider = _require_provider(provider)
    normalized_repository = _require_repository(repository)
    source = _source(
        "ci", "ci_gate.evaluate", normalized_provider, {
            "provider": normalized_provider,
            "repository": normalized_repository,
            "head_sha": head,
        }, observed_at,
    )
    if gate["total"] == 0:
        if gate["pending"] or gate["failing"] or (gate["passed"] and not gate["waived"]):
            raise ReceiptStoreError("divergent_ci_gate")
        return _observation("ci", source, None)
    if gate["waived"]:
        raise ReceiptStoreError("divergent_ci_gate")
    if gate["pending"]:
        status = "in-progress"
    elif gate["failing"]:
        status = "failure"
    elif gate["passed"]:
        status = "success"
    else:
        status = "neutral"
    receipt = {
        **_provider_receipt_base(normalized_provider, normalized_repository, None),
        "status": status,
        "run_id": None,
        "head_sha": head,
    }
    return _observation("ci", source, receipt)


def merge_receipt(
    provider: str, repository: str, pr_number: int, pr: PullRequest, merge_sha: str,
    *, operation: str, observed_at: int | None = None,
) -> ReceiptObservation:
    """Project only a successful CodeHost merge response with one exact SHA."""
    if (not isinstance(pr, PullRequest) or type(pr_number) is not int or pr_number < 1
            or pr.number != pr_number or pr.merged is not True):
        raise ReceiptStoreError("invalid_merge")
    exact_merge_sha = _require_sha(merge_sha, "merge_sha")
    if pr.merge_sha is not None and pr.merge_sha != exact_merge_sha:
        raise ReceiptStoreError("divergent_merge_sha")
    if pr.merge_sha is None and pr.sha != exact_merge_sha:
        raise ReceiptStoreError("divergent_merge_sha")
    normalized_provider = _require_provider(provider)
    normalized_repository = _require_repository(repository)
    receipt = {
        **_provider_receipt_base(normalized_provider, normalized_repository, pr.url),
        "pr_number": pr_number,
        "merged": True,
        "merge_sha": exact_merge_sha,
    }
    source = _source(
        "merge", operation, normalized_provider, {
            "provider": normalized_provider,
            "repository": normalized_repository,
            "pr_number": pr_number,
        }, observed_at,
    )
    return _observation("merge", source, receipt)


def epic_closure_receipt(
    tracker: str, issue_id: str, outcome: EpicClosureOutcome, *,
    observed_at: int | None = None,
) -> ReceiptObservation:
    """Project the already-validated atomic tracker closure outcome."""
    epic_id = _require_issue(issue_id)
    if (type(outcome) is not EpicClosureOutcome
            or outcome.receipt.parent_id != epic_id
            or outcome.closed_parent_version != outcome.receipt.parent_version + 1
            or not outcome.receipt.children):
        raise ReceiptStoreError("invalid_epic_closure")
    normalized_tracker = _require_identifier(tracker, "tracker_adapter")
    children = []
    for child in outcome.receipt.children:
        if (not isinstance(child.id, str) or _ISSUE_ID.fullmatch(child.id) is None
                or type(child.version) is not int or child.version < 1
                or child.state not in {"done", "dropped"}):
            raise ReceiptStoreError("invalid_epic_closure")
        children.append({
            "issue_id": child.id, "version": child.version, "state": child.state,
        })
    child_ids = [child["issue_id"] for child in children]
    if (child_ids != sorted(child_ids) or len(set(child_ids)) != len(child_ids)
            or len(children) > 100):
        raise ReceiptStoreError("invalid_epic_closure")
    receipt = {
        "epic_id": epic_id,
        "state": "closed",
        "receipt_id": _require_identifier(outcome.audit_id, "closure_receipt_id"),
        "parent_version": outcome.closed_parent_version,
        "children": children,
    }
    receipt["closure_digest"] = _digest({
        "contract": EPIC_CLOSURE_COORDINATE_VERSION,
        **receipt,
    })
    source = _source(
        "epic_closure", "tracker.close_epic", normalized_tracker, {
            "tracker": normalized_tracker,
            "epic_id": epic_id,
        }, observed_at,
    )
    return _observation("epic_closure", source, receipt)


class ExecutionReceiptStore:
    """Immutable attempt/issue binding with fail-closed cross-attempt ownership."""

    def __init__(self, directory: str | os.PathLike, *, max_bindings: int = _MAX_BINDINGS):
        if type(max_bindings) is not int or max_bindings < 1:
            raise ValueError("invalid receipt-store bound")
        self.directory = Path(directory).expanduser().resolve()
        self.max_bindings = max_bindings
        self.path = self.directory / "receipts.json"
        self.lock_path = self.directory / ".receipts.lock"

    @staticmethod
    def _empty() -> dict:
        return {"schema": SCHEMA_VERSION, "bindings": {}, "owners": {}}

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _load(self) -> dict:
        if not self.path.exists():
            return self._empty()
        try:
            raw = self.path.read_bytes()
            if len(raw) > _MAX_BYTES:
                raise ValueError
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ReceiptStoreError("receipt_store_unreadable") from error
        if (not isinstance(value, dict) or set(value) != {"schema", "bindings", "owners"}
                or value.get("schema") != SCHEMA_VERSION
                or not isinstance(value.get("bindings"), dict)
                or not isinstance(value.get("owners"), dict)):
            raise ReceiptStoreError("receipt_store_invalid")
        self._validate_state(value)
        return value

    def _validate_state(self, value: Mapping[str, Any]) -> None:
        bindings = value["bindings"]
        owners = value["owners"]
        if len(bindings) > self.max_bindings:
            raise ReceiptStoreError("receipt_store_invalid")
        validators = {
            "pr": _validate_pr_wire,
            "review": _validate_review_wire,
            "ci": _validate_ci_wire,
            "merge": _validate_merge_wire,
            "epic_closure": _validate_epic_closure_wire,
        }
        required_owners = {}
        for key, binding in bindings.items():
            if (not isinstance(key, str) or _DIGEST.fullmatch(key) is None
                    or not isinstance(binding, dict)
                    or set(binding) != {
                        "attempt_id", "issue_id", "receipts", "sources",
                        "states", "rejections",
                    }):
                raise ReceiptStoreError("receipt_store_invalid")
            attempt = _require_identifier(binding.get("attempt_id"), "attempt_id")
            issue = _require_issue(binding.get("issue_id"))
            if key != self._binding_key(attempt, issue):
                raise ReceiptStoreError("receipt_store_invalid")
            receipt_map = binding.get("receipts")
            source_map = binding.get("sources")
            states = binding.get("states")
            rejections = binding.get("rejections")
            if (not isinstance(receipt_map, dict) or set(receipt_map) != set(RECEIPT_KINDS)
                    or not isinstance(source_map, dict)
                    or set(source_map) != set(RECEIPT_KINDS)
                    or not isinstance(states, dict) or set(states) != set(RECEIPT_KINDS)
                    or not isinstance(rejections, dict)
                    or set(rejections) != set(RECEIPT_KINDS)):
                raise ReceiptStoreError("receipt_store_invalid")
            for kind in RECEIPT_KINDS:
                state = states[kind]
                receipt = receipt_map[kind]
                source = source_map[kind]
                rejection = rejections[kind]
                if state not in {"unavailable", "available", "rejected"}:
                    raise ReceiptStoreError("receipt_store_invalid")
                if (state == "available"
                        and (not isinstance(receipt, dict) or rejection is not None)):
                    raise ReceiptStoreError("receipt_store_invalid")
                if (state == "unavailable"
                        and (receipt is not None or rejection is not None)):
                    raise ReceiptStoreError("receipt_store_invalid")
                if (state == "rejected"
                        and (receipt is not None or rejection not in _REJECTION_REASONS)):
                    raise ReceiptStoreError("receipt_store_invalid")
                if source is None:
                    if state != "unavailable" or receipt is not None:
                        raise ReceiptStoreError("receipt_store_invalid")
                    continue
                if not isinstance(source, dict):
                    raise ReceiptStoreError("receipt_store_invalid")
                _validate_source(kind, source)
                source_digest = _source_digest(kind, source)
                if receipt is not None:
                    validators[kind](receipt)
                    _validate_source_receipt(kind, source, receipt)
                if state == "unavailable":
                    if owners.get(source_digest) == key:
                        raise ReceiptStoreError("receipt_store_invalid")
                    continue
                if rejection == "source-owned":
                    owner = owners.get(source_digest)
                    if owner is None or owner == key:
                        raise ReceiptStoreError("receipt_store_invalid")
                    continue
                if (state == "available"
                        or rejection == "divergent-observation"):
                    previous_owner = required_owners.get(source_digest)
                    if previous_owner is not None and previous_owner != key:
                        raise ReceiptStoreError("receipt_store_invalid")
                    required_owners[source_digest] = key
        if any(owners.get(source_digest) != key
               for source_digest, key in required_owners.items()):
            raise ReceiptStoreError("receipt_store_invalid")
        for source_digest, key in owners.items():
            if (not isinstance(source_digest, str)
                    or _DIGEST.fullmatch(source_digest) is None
                    or not isinstance(key, str) or key not in bindings):
                raise ReceiptStoreError("receipt_store_invalid")
            owner = bindings[key]
            matches = [
                kind for kind in RECEIPT_KINDS
                if owner["sources"][kind] is not None
                and _source_digest(kind, owner["sources"][kind]) == source_digest
            ]
            if len(matches) != 1:
                raise ReceiptStoreError("receipt_store_invalid")
            kind = matches[0]
            if (owner["states"][kind] == "unavailable"
                    or owner["rejections"][kind] == "source-owned"):
                raise ReceiptStoreError("receipt_store_invalid")

    def _save(self, value: Mapping[str, Any]) -> None:
        encoded = _canonical(value)
        if len(encoded) > _MAX_BYTES:
            raise ReceiptStoreError("receipt_store_full")
        descriptor, temporary = tempfile.mkstemp(prefix=".receipts.", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @staticmethod
    def _binding_key(attempt_id: str, issue_id: str) -> str:
        return _digest({"attempt_id": attempt_id, "issue_id": issue_id})

    @staticmethod
    def _new_binding(attempt_id: str, issue_id: str) -> dict:
        return {
            "attempt_id": attempt_id,
            "issue_id": issue_id,
            "receipts": {kind: None for kind in RECEIPT_KINDS},
            "sources": {kind: None for kind in RECEIPT_KINDS},
            "states": {kind: "unavailable" for kind in RECEIPT_KINDS},
            "rejections": {kind: None for kind in RECEIPT_KINDS},
        }

    def record(
        self, attempt_id: str, issue_id: str, kind: str,
        observation: ReceiptObservation | None,
    ) -> str:
        """Record one source, allowing only unavailable-to-available enrichment."""
        attempt = _require_identifier(attempt_id, "attempt_id")
        issue = _require_issue(issue_id)
        if kind not in RECEIPT_KINDS:
            raise ReceiptStoreError("invalid_receipt_kind")
        if observation is None:
            return "unavailable"
        source, receipt = _normalize_observation(kind, observation)
        source_digest = _source_digest(kind, source)
        key = self._binding_key(attempt, issue)
        with self._locked():
            state = self._load()
            binding = state["bindings"].get(key)
            if binding is None:
                if len(state["bindings"]) >= self.max_bindings:
                    return "rejected"
                binding = self._new_binding(attempt, issue)
                state["bindings"][key] = binding
            if binding.get("attempt_id") != attempt or binding.get("issue_id") != issue:
                raise ReceiptStoreError("divergent_binding_identity")
            if binding["states"].get(kind) == "rejected":
                return "rejected"
            existing_source = binding["sources"].get(kind)
            if existing_source is not None:
                if _source_digest(kind, existing_source) != source_digest:
                    binding["receipts"][kind] = None
                    binding["states"][kind] = "rejected"
                    binding["rejections"][kind] = "divergent-source"
                    self._save(state)
                    return "rejected"
                existing_receipt = binding["receipts"].get(kind)
                if existing_receipt == receipt:
                    return "unchanged"
                # Unavailability is not a competing fact and cannot erase a
                # receipt already observed for this exact stable source.
                if receipt is None:
                    return "unchanged"
                if existing_receipt is None:
                    owner = state["owners"].get(source_digest)
                    if owner is not None and owner != key:
                        binding["sources"][kind] = source
                        binding["states"][kind] = "rejected"
                        binding["rejections"][kind] = "source-owned"
                        self._save(state)
                        return "rejected"
                    binding["receipts"][kind] = receipt
                    binding["sources"][kind] = source
                    binding["states"][kind] = "available"
                    binding["rejections"][kind] = None
                    state["owners"][source_digest] = key
                    self._save(state)
                    return "recorded"
                binding["receipts"][kind] = None
                binding["states"][kind] = "rejected"
                binding["rejections"][kind] = "divergent-observation"
                self._save(state)
                return "rejected"
            owner = state["owners"].get(source_digest)
            if receipt is not None and owner is not None and owner != key:
                binding["sources"][kind] = source
                binding["states"][kind] = "rejected"
                binding["rejections"][kind] = "source-owned"
                self._save(state)
                return "rejected"
            binding["receipts"][kind] = receipt
            binding["sources"][kind] = source
            binding["states"][kind] = (
                "available" if receipt is not None else "unavailable"
            )
            if receipt is not None:
                state["owners"][source_digest] = key
            self._save(state)
        return "recorded" if receipt is not None else "unavailable"

    def receipts_for(self, attempt_id: str, issue_id: str) -> dict[str, Any]:
        """Return the exact FOUNDRY-88 map; rejected/absent facts remain null."""
        attempt = _require_identifier(attempt_id, "attempt_id")
        issue = _require_issue(issue_id)
        with self._locked():
            binding = self._load()["bindings"].get(self._binding_key(attempt, issue))
        if binding is None:
            return {kind: None for kind in RECEIPT_KINDS}
        if binding.get("attempt_id") != attempt or binding.get("issue_id") != issue:
            raise ReceiptStoreError("divergent_binding_identity")
        return {
            kind: binding["receipts"].get(kind)
            if binding["states"].get(kind) == "available" else None
            for kind in RECEIPT_KINDS
        }

    def for_attempt(self, attempt_id: str) -> tuple[dict[str, Any], ...]:
        """Bounded publisher seam retaining source provenance and rejection state."""
        attempt = _require_identifier(attempt_id, "attempt_id")
        with self._locked():
            bindings = tuple(self._load()["bindings"].values())
        rows = []
        for binding in bindings:
            if binding.get("attempt_id") != attempt:
                continue
            issue = _require_issue(binding.get("issue_id"))
            rows.append({
                "attempt_id": attempt,
                "issue_id": issue,
                "receipts": {
                    kind: binding["receipts"].get(kind)
                    if binding["states"].get(kind) == "available" else None
                    for kind in RECEIPT_KINDS
                },
                "receipt_states": dict(binding["states"]),
                "receipt_rejections": dict(binding["rejections"]),
                "receipt_sources": dict(binding["sources"]),
            })
        return tuple(sorted(rows, key=lambda row: row["issue_id"]))


def _validate_exact(value: Mapping[str, Any], keys: set[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ReceiptStoreError("invalid_receipt_schema")


def _validate_codehost_wire(value: Mapping[str, Any]) -> None:
    _require_provider(value.get("provider"))
    _require_repository(value.get("repository"))
    if value.get("url") is not None:
        _require_url(value["url"])


def _validate_pr_wire(value: Mapping[str, Any]) -> None:
    _validate_exact(value, {
        "provider", "repository", "url", "number", "state", "head_sha",
        "base_sha",
    })
    _validate_codehost_wire(value)
    if type(value["number"]) is not int or value["number"] < 1 or value["state"] not in {"open", "closed", "merged"}:
        raise ReceiptStoreError("invalid_pr_receipt")
    for key in ("head_sha", "base_sha"):
        if value[key] is not None:
            _require_sha(value[key], key)


def _validate_review_wire(value: Mapping[str, Any]) -> None:
    _validate_exact(value, {
        "provider", "repository", "url", "pr_number", "verdict", "review_id",
        "reviewer", "head_sha",
    })
    _validate_codehost_wire(value)
    if type(value["pr_number"]) is not int or value["pr_number"] < 1 or value["verdict"] not in {"approved", "changes-requested", "commented", "dismissed"}:
        raise ReceiptStoreError("invalid_review_receipt")
    for key in ("review_id", "reviewer"):
        if value[key] is not None:
            _require_identifier(value[key], key)
    if value["head_sha"] is not None:
        _require_sha(value["head_sha"], "head_sha")


def _validate_ci_wire(value: Mapping[str, Any]) -> None:
    _validate_exact(value, {
        "provider", "repository", "url", "status", "run_id", "head_sha",
    })
    _validate_codehost_wire(value)
    if value["status"] not in {"queued", "in-progress", "success", "failure", "cancelled", "skipped", "neutral"}:
        raise ReceiptStoreError("invalid_ci_receipt")
    if value["run_id"] is not None:
        _require_identifier(value["run_id"], "run_id")
    if value["head_sha"] is not None:
        _require_sha(value["head_sha"], "head_sha")


def _validate_merge_wire(value: Mapping[str, Any]) -> None:
    _validate_exact(value, {
        "provider", "repository", "url", "pr_number", "merged", "merge_sha",
    })
    _validate_codehost_wire(value)
    if type(value["pr_number"]) is not int or value["pr_number"] < 1 or value["merged"] is not True:
        raise ReceiptStoreError("invalid_merge_receipt")
    if value["merge_sha"] is not None:
        _require_sha(value["merge_sha"], "merge_sha")


def _validate_epic_closure_wire(value: Mapping[str, Any]) -> None:
    compact = {"epic_id", "state", "receipt_id"}
    complete = compact | {"parent_version", "children", "closure_digest"}
    if frozenset(value) not in {frozenset(compact), frozenset(complete)}:
        raise ReceiptStoreError("invalid_epic_closure_receipt")
    _require_issue(value.get("epic_id"))
    if value.get("state") != "closed":
        raise ReceiptStoreError("invalid_epic_closure_receipt")
    if value["receipt_id"] is not None:
        _require_identifier(value["receipt_id"], "receipt_id")
    if set(value) == compact:
        return
    if type(value["parent_version"]) is not int or value["parent_version"] < 1:
        raise ReceiptStoreError("invalid_epic_closure_receipt")
    children = value["children"]
    if not isinstance(children, list) or not 1 <= len(children) <= 100:
        raise ReceiptStoreError("invalid_epic_closure_receipt")
    child_ids = []
    for child in children:
        _validate_exact(child, {"issue_id", "version", "state"})
        child_ids.append(_require_issue(child["issue_id"]))
        if (type(child["version"]) is not int or child["version"] < 1
                or child["state"] not in {"done", "dropped"}):
            raise ReceiptStoreError("invalid_epic_closure_receipt")
    if child_ids != sorted(child_ids) or len(child_ids) != len(set(child_ids)):
        raise ReceiptStoreError("invalid_epic_closure_receipt")
    if (not isinstance(value["closure_digest"], str)
            or _DIGEST.fullmatch(value["closure_digest"]) is None
            or value["closure_digest"] != _digest({
                "contract": EPIC_CLOSURE_COORDINATE_VERSION,
                **{key: value[key] for key in complete - {"closure_digest"}},
            })):
        raise ReceiptStoreError("invalid_epic_closure_receipt")


def _validate_source(kind: str, value: Mapping[str, Any]) -> None:
    if kind not in RECEIPT_KINDS:
        raise ReceiptStoreError("invalid_receipt_kind")
    _validate_exact(value, {
        "operation", "provenance", "adapter", "adapter_version", "identity",
        "observed_at",
    })
    if (value["operation"] not in _SOURCE_OPERATIONS[kind]
            or value["provenance"] != _SOURCE_PROVENANCE[kind]
            or value["adapter_version"] != SOURCE_ADAPTER_VERSION):
        raise ReceiptStoreError("invalid_receipt_source")
    adapter = _require_identifier(value["adapter"], "source_adapter")
    _require_observed_at(value["observed_at"])
    identity = value["identity"]
    if not isinstance(identity, Mapping):
        raise ReceiptStoreError("invalid_receipt_source")
    if kind == "pr":
        _validate_exact(identity, {"provider", "repository", "number"})
        provider = _require_provider(identity["provider"])
        _require_repository(identity["repository"])
        if type(identity["number"]) is not int or identity["number"] < 1:
            raise ReceiptStoreError("invalid_receipt_source")
        expected_adapter = provider
    elif kind == "review":
        _validate_exact(identity, {
            "provider", "repository", "pr_number", "review_id",
        })
        _require_provider(identity["provider"])
        _require_repository(identity["repository"])
        if (type(identity["pr_number"]) is not int or identity["pr_number"] < 1
                or not isinstance(identity["review_id"], str)
                or _DIGEST.fullmatch(identity["review_id"]) is None):
            raise ReceiptStoreError("invalid_receipt_source")
        expected_adapter = "foundry-review-proof"
    elif kind == "ci":
        _validate_exact(identity, {"provider", "repository", "head_sha"})
        provider = _require_provider(identity["provider"])
        _require_repository(identity["repository"])
        _require_sha(identity["head_sha"], "source_head_sha")
        expected_adapter = provider
    elif kind == "merge":
        _validate_exact(identity, {"provider", "repository", "pr_number"})
        provider = _require_provider(identity["provider"])
        _require_repository(identity["repository"])
        if type(identity["pr_number"]) is not int or identity["pr_number"] < 1:
            raise ReceiptStoreError("invalid_receipt_source")
        expected_adapter = provider
    else:
        _validate_exact(identity, {"tracker", "epic_id"})
        expected_adapter = _require_identifier(identity["tracker"], "tracker_adapter")
        _require_issue(identity["epic_id"])
    if adapter != expected_adapter:
        raise ReceiptStoreError("invalid_receipt_source")


def _source_digest(kind: str, source: Mapping[str, Any]) -> str:
    _validate_source(kind, source)
    return _digest({"kind": kind, "identity": source["identity"]})


def _validate_source_receipt(
    kind: str, source: Mapping[str, Any], receipt: Mapping[str, Any],
) -> None:
    identity = source["identity"]
    coordinates = {
        "pr": ("provider", "repository", "number"),
        "review": ("provider", "repository", "pr_number", "review_id"),
        "ci": ("provider", "repository", "head_sha"),
        "merge": ("provider", "repository", "pr_number"),
        "epic_closure": ("epic_id",),
    }[kind]
    if any(receipt.get(key) != identity.get(key) for key in coordinates):
        raise ReceiptStoreError("divergent_receipt_source")


def _normalize_observation(
    kind: str, observation: ReceiptObservation,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(observation, ReceiptObservation) or observation.kind != kind:
        raise ReceiptStoreError("invalid_receipt_observation")
    if not isinstance(observation.source, Mapping):
        raise ReceiptStoreError("invalid_receipt_observation")
    source = dict(observation.source)
    if isinstance(source.get("identity"), Mapping):
        source["identity"] = dict(source["identity"])
    _validate_source(kind, source)
    if observation.receipt is None:
        return source, None
    if not isinstance(observation.receipt, Mapping):
        raise ReceiptStoreError("invalid_receipt_observation")
    receipt = dict(observation.receipt)
    validators = {
        "pr": _validate_pr_wire,
        "review": _validate_review_wire,
        "ci": _validate_ci_wire,
        "merge": _validate_merge_wire,
        "epic_closure": _validate_epic_closure_wire,
    }
    validators[kind](receipt)
    _validate_source_receipt(kind, source, receipt)
    return source, receipt


def recorder_from_environment() -> tuple[ExecutionReceiptStore, str] | None:
    """Resolve only non-secret runtime binding; ordinary local calls are a no-op."""
    attempt = os.environ.get(ATTEMPT_ID_ENV)
    if attempt is None:
        return None
    try:
        _require_identifier(attempt, "attempt_id")
    except ReceiptStoreError:
        return None
    directory = os.environ.get(RECEIPT_DIRECTORY_ENV)
    if directory is None:
        directory = str(Path(registry.data_dir()) / "execution-receipts")
    return ExecutionReceiptStore(directory), attempt


def observe(
    issue_id: str, kind: str, observation: ReceiptObservation | None,
) -> str:
    """Best-effort passive integration point; never changes its source operation."""
    resolved = recorder_from_environment()
    if resolved is None:
        return "unavailable"
    store, attempt = resolved
    try:
        return store.record(attempt, issue_id, kind, observation)
    except (OSError, ReceiptStoreError, ValueError):
        return "rejected"
