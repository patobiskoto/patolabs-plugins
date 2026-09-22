"""Canonical, read-only evidence envelope for cockpit projections.

The envelope composes existing Foundry review coordinates, a content-free test
receipt, and both GitHub CI verdict sources.  It never mutates a tracker or code
host and its ``GO`` projection is advisory: existing Foundry gates remain the
only merge and lifecycle authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterable, Mapping
from typing import Any

from foundry.models import Check, PullRequest
from foundry.registry import canonical_repository_identity


ENVELOPE_SCHEMA = "foundry-evidence-envelope.v1"
TEST_RECEIPT_SCHEMA = "foundry-test-receipt.v1"
CI_RECEIPT_SCHEMA = "foundry-ci-receipt.v1"
VERDICT_SCHEMA = "foundry-evidence-verdict.v1"
MAX_EVIDENCE_AGE_MS = 15 * 60 * 1_000
MAX_CLOCK_SKEW_MS = 60 * 1_000

_ISSUE = re.compile(r"[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TEST_STATUSES = frozenset({"passed", "failed", "pending", "unavailable"})
_CI_FAILURES = frozenset({
    "action_required", "cancelled", "failure", "stale", "startup_failure",
    "timed_out",
})
_CI_PENDING = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})


class EvidencePlaneError(ValueError):
    """An evidence input is malformed; the verifier projects it as UNKNOWN."""


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


def _detached(value: object) -> Any:
    return json.loads(_canonical(value))


def _closed(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise EvidencePlaneError(f"{label} schema is incomplete or unsupported")


def _repository(value: object) -> str:
    if not isinstance(value, str):
        raise EvidencePlaneError("repository identity is invalid")
    try:
        canonical = canonical_repository_identity(value)
    except ValueError:
        raise EvidencePlaneError("repository identity is invalid") from None
    if value != canonical:
        raise EvidencePlaneError("repository identity is not canonical")
    return canonical


def _issue(value: object) -> str:
    if not isinstance(value, str) or _ISSUE.fullmatch(value) is None:
        raise EvidencePlaneError("issue identity is invalid")
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise EvidencePlaneError(f"{label} must be an exact SHA")
    return value


def _digest_value(value: object, label: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise EvidencePlaneError(f"{label} must be a digest")
    return value


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise EvidencePlaneError(f"{label} must be a positive integer")
    return value


def _timestamp(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 4_102_444_800_000:
        raise EvidencePlaneError(f"{label} is invalid")
    return value


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def create_test_receipt(
    *,
    repository: str,
    issue_id: str,
    head_sha: str,
    diff_hash: str,
    status: str,
    result_digest: str | None,
) -> dict[str, Any]:
    """Create a content-free integrity receipt for an already-run test action."""
    if status not in _TEST_STATUSES:
        raise EvidencePlaneError("test status is unsupported")
    if status in {"passed", "failed"}:
        result_digest = _digest_value(result_digest, "test result")
    elif result_digest is not None:
        raise EvidencePlaneError("non-terminal test receipt cannot carry a result")
    receipt = {
        "schema": TEST_RECEIPT_SCHEMA,
        "observed_at": _now_ms(),
        "repository": _repository(repository),
        "issue": _issue(issue_id),
        "head_sha": _sha(head_sha, "test head"),
        "diff_hash": _digest_value(diff_hash, "test diff"),
        "status": status,
        "result_digest": result_digest,
    }
    receipt["receipt_id"] = _digest(receipt)
    return _detached(receipt)


def _validated_test_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidencePlaneError("test receipt is unavailable")
    _closed(value, frozenset({
        "schema", "receipt_id", "repository", "issue", "head_sha",
        "diff_hash", "status", "result_digest", "observed_at",
    }), "test receipt")
    receipt = dict(value)
    receipt_id = _digest_value(receipt.pop("receipt_id", None), "test receipt id")
    if receipt.get("schema") != TEST_RECEIPT_SCHEMA or _digest(receipt) != receipt_id:
        raise EvidencePlaneError("test receipt integrity is invalid")
    canonical = create_test_receipt(
        repository=receipt.get("repository"),
        issue_id=receipt.get("issue"),
        head_sha=receipt.get("head_sha"),
        diff_hash=receipt.get("diff_hash"),
        status=receipt.get("status"),
        result_digest=receipt.get("result_digest"),
    )
    canonical["observed_at"] = _timestamp(
        receipt.get("observed_at"),
        "test observation",
    )
    canonical_without_id = dict(canonical)
    canonical_without_id.pop("receipt_id")
    canonical["receipt_id"] = _digest(canonical_without_id)
    if canonical["receipt_id"] != receipt_id:
        raise EvidencePlaneError("test receipt is not canonical")
    return canonical


def _empty_review(state: str) -> dict[str, Any]:
    return {
        "state": state,
        "proof_id": None,
        "issue": None,
        "ac_digest": None,
        "base_sha": None,
        "head_sha": None,
        "diff_hash": None,
        "verdict": None,
    }


def _review_evidence(value: object) -> dict[str, Any]:
    if value is None:
        return _empty_review("unavailable")
    try:
        if not isinstance(value, Mapping):
            raise EvidencePlaneError("review proof is invalid")
        _closed(value, frozenset({
            "schema_version", "proof_id", "issue", "review", "coordinates", "quality",
        }), "review proof")
        proof = dict(value)
        proof_id = _digest_value(proof.pop("proof_id", None), "review proof id")
        if proof.get("schema_version") != 1 or _digest(proof) != proof_id:
            raise EvidencePlaneError("review proof integrity is invalid")
        issue = proof.get("issue")
        review = proof.get("review")
        coordinates = proof.get("coordinates")
        if not isinstance(issue, Mapping) or not isinstance(review, Mapping) or not isinstance(
            coordinates, Mapping,
        ):
            raise EvidencePlaneError("review proof is incomplete")
        _closed(issue, frozenset({"id", "ac_digest", "criteria"}), "review issue")
        _closed(review, frozenset({"role", "generation", "claim_digest"}), "review identity")
        _closed(coordinates, frozenset({"base", "head", "diff_hash"}), "review coordinates")
        criteria = issue.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise EvidencePlaneError("review criteria are incomplete")
        verdicts: list[str] = []
        for index, criterion in enumerate(criteria, start=1):
            if not isinstance(criterion, Mapping):
                raise EvidencePlaneError("review criterion is invalid")
            _closed(criterion, frozenset({"id", "digest", "verdict"}), "review criterion")
            if criterion.get("id") != f"ac-{index}":
                raise EvidencePlaneError("review criteria are not canonical")
            _digest_value(criterion.get("digest"), "review criterion")
            if criterion.get("verdict") not in {
                "pass", "fail", "not_covered", "contradicted",
            }:
                raise EvidencePlaneError("review verdict is invalid")
            verdicts.append(str(criterion["verdict"]))
        if (review.get("role") != "reviewer"
                or type(review.get("generation")) is not int
                or review["generation"] < 1):
            raise EvidencePlaneError("review identity is invalid")
        _digest_value(review.get("claim_digest"), "review claim")
        quality = proof.get("quality")
        if quality not in {"mergeable", "blocked"}:
            raise EvidencePlaneError("review quality is invalid")
        return {
            "state": "available",
            "proof_id": proof_id,
            "issue": _issue(issue.get("id")),
            "ac_digest": _digest_value(issue.get("ac_digest"), "review AC"),
            "base_sha": _sha(coordinates.get("base"), "review base"),
            "head_sha": _sha(coordinates.get("head"), "review head"),
            "diff_hash": _digest_value(coordinates.get("diff_hash"), "review diff"),
            "verdict": (
                "approved"
                if quality == "mergeable" and all(item == "pass" for item in verdicts)
                else "blocked"
            ),
        }
    except (EvidencePlaneError, TypeError, ValueError):
        return _empty_review("invalid")


def _empty_tests(state: str) -> dict[str, Any]:
    return {
        "state": state,
        "receipt_id": None,
        "repository": None,
        "issue": None,
        "head_sha": None,
        "diff_hash": None,
        "status": None,
        "result_digest": None,
        "observed_at": None,
    }


def _test_evidence(value: object) -> dict[str, Any]:
    if value is None:
        return _empty_tests("unavailable")
    try:
        receipt = _validated_test_receipt(value)
    except (EvidencePlaneError, TypeError, ValueError):
        return _empty_tests("invalid")
    return {
        "state": "available",
        **{key: receipt[key] for key in (
            "receipt_id", "repository", "issue", "head_sha", "diff_hash",
            "status", "result_digest",
            "observed_at",
        )},
    }


def _ci_source(value: object) -> str:
    if value not in {"check_runs", "commit_statuses"}:
        raise EvidencePlaneError("CI source is unsupported")
    return str(value)


def _ci_counts(checks: Iterable[Check]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    counts = {key: 0 for key in (
        "total", "success", "pending", "failing", "skipped", "neutral",
    )}
    if not isinstance(checks, (list, tuple)):
        raise EvidencePlaneError("CI observation is unsupported")
    counts["total"] = len(checks)
    normalized: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, Check) or not isinstance(check.name, str) or not check.name:
            raise EvidencePlaneError("CI observation is unsupported")
        if check.status in _CI_PENDING:
            if check.conclusion is not None:
                raise EvidencePlaneError("CI observation is unsupported")
            counts["pending"] += 1
        elif check.status != "completed" or not isinstance(check.conclusion, str):
            raise EvidencePlaneError("CI observation is unsupported")
        elif check.conclusion == "success":
            counts["success"] += 1
        elif check.conclusion == "skipped":
            counts["skipped"] += 1
        elif check.conclusion == "neutral":
            counts["neutral"] += 1
        elif check.conclusion in _CI_FAILURES:
            counts["failing"] += 1
        else:
            raise EvidencePlaneError("CI observation is unsupported")
        normalized.append({"status": check.status, "conclusion": check.conclusion})
    return counts, normalized


def create_ci_receipt(
    *, source: str, repository: str, head_sha: str, observed_at: int,
    checks: Iterable[Check],
) -> dict[str, Any]:
    """Bind one fresh, normalized CI-source observation to its exact coordinates."""
    counts, normalized = _ci_counts(checks)
    receipt = {
        "schema": CI_RECEIPT_SCHEMA,
        "source": _ci_source(source),
        "repository": _repository(repository),
        "head_sha": _sha(head_sha, "CI head"),
        "observed_at": _timestamp(observed_at, "CI observation"),
        **counts,
        "observation_digest": _digest(normalized),
    }
    receipt["receipt_id"] = _digest(receipt)
    return _detached(receipt)


def _validated_ci_receipt(value: object, *, source: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidencePlaneError("CI receipt is unavailable")
    _closed(value, frozenset({
        "schema", "receipt_id", "source", "repository", "head_sha", "observed_at",
        "total", "success", "pending", "failing", "skipped", "neutral",
        "observation_digest",
    }), "CI receipt")
    receipt = dict(value)
    receipt_id = _digest_value(receipt.pop("receipt_id", None), "CI receipt id")
    if receipt.get("schema") != CI_RECEIPT_SCHEMA or _digest(receipt) != receipt_id:
        raise EvidencePlaneError("CI receipt integrity is invalid")
    if _ci_source(receipt.get("source")) != source:
        raise EvidencePlaneError("CI receipt source is invalid")
    _repository(receipt.get("repository"))
    _sha(receipt.get("head_sha"), "CI receipt head")
    _timestamp(receipt.get("observed_at"), "CI receipt observation")
    counts = [receipt.get(key) for key in (
        "total", "success", "pending", "failing", "skipped", "neutral",
    )]
    if any(type(count) is not int or count < 0 for count in counts):
        raise EvidencePlaneError("CI receipt counts are invalid")
    if receipt["total"] != sum(counts[1:]):
        raise EvidencePlaneError("CI receipt counts are inconsistent")
    _digest_value(receipt.get("observation_digest"), "CI observation")
    return {"receipt_id": receipt_id, **_detached(receipt)}


def _coordinates(
    *, repository: str, issue_id: str, ac_digest: str, pr: PullRequest, diff_hash: str,
) -> dict[str, Any]:
    if not isinstance(pr, PullRequest):
        raise EvidencePlaneError("PR coordinates are unavailable")
    return {
        "repository": _repository(repository),
        "issue": _issue(issue_id),
        "ac_digest": _digest_value(ac_digest, "AC"),
        "pr_number": _positive_integer(pr.number, "PR number"),
        "base_sha": _sha(pr.base_sha, "PR base"),
        "head_sha": _sha(pr.sha, "PR head"),
        "diff_hash": _digest_value(diff_hash, "PR diff"),
    }


def evidence_envelope(
    *,
    repository: str,
    issue_id: str,
    ac_digest: str,
    pr: PullRequest,
    diff_hash: str,
    review_proof: Mapping[str, Any] | None,
    test_receipt: Mapping[str, Any] | None,
    check_runs_receipt: Mapping[str, Any] | None,
    commit_statuses_receipt: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compose content-free evidence; no result from this function is an authority."""
    coordinates = _coordinates(
        repository=repository,
        issue_id=issue_id,
        ac_digest=ac_digest,
        pr=pr,
        diff_hash=diff_hash,
    )
    envelope = {
        "schema": ENVELOPE_SCHEMA,
        "observed_at": _now_ms(),
        "coordinates": coordinates,
        "evidence": {
            "review": _review_evidence(review_proof),
            "tests": _test_evidence(test_receipt),
            "ci": {
                "check_runs": _ci_evidence(check_runs_receipt, source="check_runs"),
                "commit_statuses": _ci_evidence(
                    commit_statuses_receipt, source="commit_statuses",
                ),
            },
        },
    }
    envelope["envelope_id"] = _digest(envelope)
    return _detached(envelope)


def _ci_evidence(value: Mapping[str, Any] | None, *, source: str) -> dict[str, Any] | None:
    """Keep only a closed receipt; composition never accepts raw provider checks."""
    if value is None:
        return None
    try:
        return _validated_ci_receipt(value, source=source)
    except (EvidencePlaneError, TypeError, ValueError):
        return None


def claude_evidence_envelope(**kwargs: Any) -> dict[str, Any]:
    """Claude Code facade over the shared deterministic envelope builder."""
    return evidence_envelope(**kwargs)


def codex_evidence_envelope(**kwargs: Any) -> dict[str, Any]:
    """Codex facade over the shared deterministic envelope builder."""
    return evidence_envelope(**kwargs)


def _validated_envelope(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidencePlaneError("envelope is unavailable")
    _closed(value, frozenset({
        "schema", "envelope_id", "observed_at", "coordinates", "evidence",
    }), "envelope")
    envelope = dict(value)
    envelope_id = _digest_value(envelope.pop("envelope_id", None), "envelope id")
    if envelope.get("schema") != ENVELOPE_SCHEMA or _digest(envelope) != envelope_id:
        raise EvidencePlaneError("envelope integrity is invalid")
    _timestamp(envelope.get("observed_at"), "envelope observation")
    coordinates = envelope.get("coordinates")
    evidence = envelope.get("evidence")
    if not isinstance(coordinates, Mapping) or not isinstance(evidence, Mapping):
        raise EvidencePlaneError("envelope is incomplete")
    _closed(coordinates, frozenset({
        "repository", "issue", "ac_digest", "pr_number", "base_sha", "head_sha",
        "diff_hash",
    }), "envelope coordinates")
    _repository(coordinates.get("repository"))
    _issue(coordinates.get("issue"))
    _digest_value(coordinates.get("ac_digest"), "envelope AC")
    _positive_integer(coordinates.get("pr_number"), "envelope PR")
    _sha(coordinates.get("base_sha"), "envelope base")
    _sha(coordinates.get("head_sha"), "envelope head")
    _digest_value(coordinates.get("diff_hash"), "envelope diff")
    _closed(evidence, frozenset({"review", "tests", "ci"}), "envelope evidence")
    review = evidence.get("review")
    tests = evidence.get("tests")
    ci = evidence.get("ci")
    if not all(isinstance(item, Mapping) for item in (review, tests, ci)):
        raise EvidencePlaneError("envelope evidence is incomplete")
    _closed(review, frozenset({
        "state", "proof_id", "issue", "ac_digest", "base_sha", "head_sha",
        "diff_hash", "verdict",
    }), "review evidence")
    if review.get("state") not in {"available", "unavailable", "invalid"}:
        raise EvidencePlaneError("review evidence state is invalid")
    if review["state"] == "available":
        _digest_value(review.get("proof_id"), "review proof")
        _issue(review.get("issue"))
        _digest_value(review.get("ac_digest"), "review AC")
        _sha(review.get("base_sha"), "review base")
        _sha(review.get("head_sha"), "review head")
        _digest_value(review.get("diff_hash"), "review diff")
        if review.get("verdict") not in {"approved", "blocked"}:
            raise EvidencePlaneError("review verdict is invalid")
    elif any(review.get(key) is not None for key in set(review) - {"state"}):
        raise EvidencePlaneError("unavailable review contains data")
    _closed(tests, frozenset({
        "state", "receipt_id", "repository", "issue", "head_sha", "diff_hash",
        "status", "result_digest", "observed_at",
    }), "test evidence")
    if tests.get("state") not in {"available", "unavailable", "invalid"}:
        raise EvidencePlaneError("test evidence state is invalid")
    if tests["state"] == "available":
        reconstructed = create_test_receipt(
            repository=tests.get("repository"),
            issue_id=tests.get("issue"),
            head_sha=tests.get("head_sha"),
            diff_hash=tests.get("diff_hash"),
            status=tests.get("status"),
            result_digest=tests.get("result_digest"),
        )
        reconstructed["observed_at"] = _timestamp(
            tests.get("observed_at"),
            "test observation",
        )
        reconstructed_without_id = dict(reconstructed)
        reconstructed_without_id.pop("receipt_id")
        reconstructed["receipt_id"] = _digest(reconstructed_without_id)
        if reconstructed["receipt_id"] != tests.get("receipt_id"):
            raise EvidencePlaneError("test evidence integrity is invalid")
    elif any(tests.get(key) is not None for key in set(tests) - {"state"}):
        raise EvidencePlaneError("unavailable test evidence contains data")
    _closed(ci, frozenset({"check_runs", "commit_statuses"}), "CI evidence")
    for source in ("check_runs", "commit_statuses"):
        row = ci.get(source)
        if row is not None:
            _validated_ci_receipt(row, source=source)
    return {"envelope_id": envelope_id, **_detached(envelope)}


def verify_evidence_envelope(
    envelope: object,
    *,
    repository: str,
    issue_id: str,
    ac_digest: str,
    pr: PullRequest,
    diff_hash: str,
    seen_envelope_ids: Iterable[str] = (),
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Project GO/STOP/UNKNOWN without granting any mutation or merge authority."""
    verified_at = _now_ms() if now_ms is None else now_ms
    try:
        verified_at = _timestamp(verified_at, "verification time")
        current = _coordinates(
            repository=repository,
            issue_id=issue_id,
            ac_digest=ac_digest,
            pr=pr,
            diff_hash=diff_hash,
        )
        canonical = _validated_envelope(envelope)
    except (EvidencePlaneError, TypeError, ValueError, RecursionError):
        return {
            "schema": VERDICT_SCHEMA,
            "envelope_id": None,
            "decision": "UNKNOWN",
            "reasons": ["incomplete-or-invalid-envelope"],
            "verified_at": verified_at if type(verified_at) is int else None,
        }
    envelope_id = canonical["envelope_id"]
    try:
        if not isinstance(seen_envelope_ids, (list, tuple, set, frozenset)):
            raise EvidencePlaneError("replay context is invalid")
        seen = frozenset(_digest_value(item, "seen envelope") for item in seen_envelope_ids)
    except (EvidencePlaneError, TypeError):
        return {
            "schema": VERDICT_SCHEMA,
            "envelope_id": envelope_id,
            "decision": "UNKNOWN",
            "reasons": ["invalid-replay-context"],
            "verified_at": verified_at,
        }
    if envelope_id in seen:
        return {
            "schema": VERDICT_SCHEMA,
            "envelope_id": envelope_id,
            "decision": "UNKNOWN",
            "reasons": ["replay"],
            "verified_at": verified_at,
        }

    stop: list[str] = []
    unknown: list[str] = []
    observed_at = canonical["observed_at"]
    if observed_at > verified_at + MAX_CLOCK_SKEW_MS:
        unknown.append("observation-in-future")
    elif verified_at - observed_at > MAX_EVIDENCE_AGE_MS:
        unknown.append("stale-envelope")

    coordinates = canonical["coordinates"]
    for key in (
        "repository", "issue", "ac_digest", "pr_number", "base_sha", "head_sha",
        "diff_hash",
    ):
        if coordinates[key] != current[key]:
            stop.append(f"coordinate-mismatch:{key}")

    review = canonical["evidence"]["review"]
    if review["state"] != "available":
        unknown.append(f"review-{review['state']}")
    else:
        if review["verdict"] != "approved":
            stop.append("review-blocked")
        for evidence_key, coordinate_key in (
            ("issue", "issue"),
            ("ac_digest", "ac_digest"),
            ("base_sha", "base_sha"),
            ("head_sha", "head_sha"),
            ("diff_hash", "diff_hash"),
        ):
            if review[evidence_key] != coordinates[coordinate_key]:
                stop.append(f"review-coordinate-mismatch:{evidence_key}")

    tests = canonical["evidence"]["tests"]
    if tests["state"] != "available":
        unknown.append(f"tests-{tests['state']}")
    else:
        test_observed_at = tests["observed_at"]
        if test_observed_at > verified_at + MAX_CLOCK_SKEW_MS:
            unknown.append("tests-observation-in-future")
        elif verified_at - test_observed_at > MAX_EVIDENCE_AGE_MS:
            unknown.append("tests-stale")
        if tests["status"] == "failed":
            stop.append("tests-failed")
        elif tests["status"] != "passed":
            unknown.append(f"tests-{tests['status']}")
        for evidence_key, coordinate_key in (
            ("repository", "repository"),
            ("issue", "issue"),
            ("head_sha", "head_sha"),
            ("diff_hash", "diff_hash"),
        ):
            if tests[evidence_key] != coordinates[coordinate_key]:
                stop.append(f"test-coordinate-mismatch:{evidence_key}")

    ci_rows = canonical["evidence"]["ci"]
    available_rows = []
    for source in ("check_runs", "commit_statuses"):
        row = ci_rows[source]
        if row is None:
            unknown.append(f"ci-{source}-unavailable")
            continue
        available_rows.append(row)
        ci_observed_at = row["observed_at"]
        if ci_observed_at > verified_at + MAX_CLOCK_SKEW_MS:
            unknown.append(f"ci-{source}-observation-in-future")
        elif verified_at - ci_observed_at > MAX_EVIDENCE_AGE_MS:
            unknown.append(f"ci-{source}-stale")
        if row["repository"] != coordinates["repository"]:
            stop.append(f"ci-coordinate-mismatch:{source}:repository")
        if row["head_sha"] != coordinates["head_sha"]:
            stop.append(f"ci-coordinate-mismatch:{source}:head_sha")
    if len(available_rows) == 2:
        total = sum(row["total"] for row in available_rows)
        success = sum(row["success"] for row in available_rows)
        pending = sum(row["pending"] for row in available_rows)
        failing = sum(row["failing"] for row in available_rows)
        if pending:
            stop.append("ci-pending")
        if failing:
            stop.append("ci-failing")
        if total == 0:
            stop.append("ci-zero-checks")
        elif success == 0:
            stop.append("ci-no-success")

    decision = "STOP" if stop else "UNKNOWN" if unknown else "GO"
    return {
        "schema": VERDICT_SCHEMA,
        "envelope_id": envelope_id,
        "decision": decision,
        "reasons": stop + unknown,
        "verified_at": verified_at,
    }


def claude_evidence_verdict(envelope: object, **kwargs: Any) -> dict[str, Any]:
    """Claude Code facade over the shared deterministic verifier."""
    return verify_evidence_envelope(envelope, **kwargs)


def codex_evidence_verdict(envelope: object, **kwargs: Any) -> dict[str, Any]:
    """Codex facade over the shared deterministic verifier."""
    return verify_evidence_envelope(envelope, **kwargs)
