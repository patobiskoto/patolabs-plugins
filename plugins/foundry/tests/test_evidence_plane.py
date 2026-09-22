import copy
import hashlib
import inspect
import json

import pytest

from foundry import evidence_plane as evidence
from foundry.models import Check, PullRequest


REPOSITORY = "github.com/patobiskoto/patolabs-plugins"
ISSUE = "FOUNDRY-161"
AC_DIGEST = "a" * 64
BASE = "b" * 40
HEAD = "c" * 40
DIFF = "d" * 64
NOW = 1_790_000_000_000


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(evidence, "_now_ms", lambda: NOW)


def pull_request(**overrides):
    values = {
        "number": 10,
        "url": "https://github.com/patobiskoto/patolabs-plugins/pull/10",
        "head": "feat/foundry-161-evidence-plane",
        "base": "main",
        "base_sha": BASE,
        "sha": HEAD,
        "merged": False,
        "state": "open",
    }
    values.update(overrides)
    return PullRequest(**values)


def review_proof(**overrides):
    proof = {
        "schema_version": 1,
        "issue": {
            "id": ISSUE,
            "ac_digest": AC_DIGEST,
            "criteria": [
                {"id": "ac-1", "digest": "1" * 64, "verdict": "pass"},
                {"id": "ac-2", "digest": "2" * 64, "verdict": "pass"},
            ],
        },
        "review": {"role": "reviewer", "generation": 1, "claim_digest": "3" * 64},
        "coordinates": {"base": BASE, "head": HEAD, "diff_hash": DIFF},
        "quality": "mergeable",
    }
    for key, value in overrides.items():
        if key in proof["coordinates"]:
            proof["coordinates"][key] = value
        elif key in proof["issue"]:
            proof["issue"][key] = value
        else:
            proof[key] = value
    proof["proof_id"] = hashlib.sha256(json.dumps(
        proof, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()
    return proof


def make_test_receipt(**overrides):
    values = {
        "repository": REPOSITORY,
        "issue_id": ISSUE,
        "head_sha": HEAD,
        "diff_hash": DIFF,
        "status": "passed",
        "result_digest": "4" * 64,
    }
    values.update(overrides)
    return evidence.create_test_receipt(**values)


def make_ci_receipt(source, checks=None, **overrides):
    values = {
        "source": source,
        "repository": REPOSITORY,
        "head_sha": HEAD,
        "observed_at": NOW,
        "checks": [Check("foundry", "completed", "success")] if checks is None else checks,
    }
    values.update(overrides)
    return evidence._create_ci_receipt(**values)


def envelope(**overrides):
    values = {
        "repository": REPOSITORY,
        "issue_id": ISSUE,
        "ac_digest": AC_DIGEST,
        "pr": pull_request(),
        "diff_hash": DIFF,
        "review_proof": review_proof(),
        "test_receipt": make_test_receipt(),
        "check_runs_receipt": make_ci_receipt("check_runs"),
        "commit_statuses_receipt": make_ci_receipt("commit_statuses", []),
    }
    values.update(overrides)
    return evidence.evidence_envelope(**values)


def verify(value, **overrides):
    values = {
        "repository": REPOSITORY,
        "issue_id": ISSUE,
        "ac_digest": AC_DIGEST,
        "pr": pull_request(),
        "diff_hash": DIFF,
        "now_ms": NOW,
    }
    values.update(overrides)
    return evidence.verify_evidence_envelope(value, **values)


def test_complete_exact_envelope_is_advisory_go_and_binds_every_required_fact():
    value = envelope()

    assert value["schema"] == evidence.ENVELOPE_SCHEMA
    assert value["coordinates"] == {
        "repository": REPOSITORY,
        "issue": ISSUE,
        "ac_digest": AC_DIGEST,
        "pr_number": 10,
        "base_sha": BASE,
        "head_sha": HEAD,
        "diff_hash": DIFF,
    }
    assert value["evidence"]["review"]["verdict"] == "approved"
    assert value["evidence"]["tests"]["status"] == "passed"
    assert value["evidence"]["ci"]["check_runs"]["source"] == "check_runs"
    assert value["evidence"]["ci"]["commit_statuses"]["source"] == "commit_statuses"
    assert verify(value)["decision"] == "GO"
    assert not {
        "credential", "command", "merge", "push", "deploy", "tracker_mutation",
    } & set(value)


@pytest.mark.parametrize(
    ("change", "decision", "reason"),
    [
        (lambda values: values.update(review_proof=None), "UNKNOWN", "review-unavailable"),
        (
            lambda values: values.update(
                review_proof=review_proof(head="e" * 40),
            ),
            "STOP",
            "review-coordinate-mismatch:head_sha",
        ),
        (
            lambda values: values.update(
                test_receipt=make_test_receipt(diff_hash="e" * 64),
            ),
            "STOP",
            "test-coordinate-mismatch:diff_hash",
        ),
        (
            lambda values: values.update(
                check_runs_receipt=make_ci_receipt(
                    "check_runs", [Check("foundry", "in_progress", None)],
                ),
            ),
            "STOP",
            "ci-pending",
        ),
        (
            lambda values: values.update(
                check_runs_receipt=make_ci_receipt("check_runs", []),
                commit_statuses_receipt=make_ci_receipt("commit_statuses", []),
            ),
            "STOP",
            "ci-zero-checks",
        ),
        (
            lambda values: values.update(
                check_runs_receipt=make_ci_receipt(
                    "check_runs", [Check("foundry", "completed", "skipped")],
                ),
            ),
            "STOP",
            "ci-no-success",
        ),
    ],
)
def test_required_negative_and_unknown_cases_are_closed(change, decision, reason):
    values = {
        "repository": REPOSITORY,
        "issue_id": ISSUE,
        "ac_digest": AC_DIGEST,
        "pr": pull_request(),
        "diff_hash": DIFF,
        "review_proof": review_proof(),
        "test_receipt": make_test_receipt(),
        "check_runs_receipt": make_ci_receipt("check_runs"),
        "commit_statuses_receipt": make_ci_receipt("commit_statuses", []),
    }
    change(values)

    verdict = verify(evidence.evidence_envelope(**values))

    assert verdict["decision"] == decision
    assert reason in verdict["reasons"]


def test_incomplete_receipt_is_unknown_without_copying_hostile_content():
    invalid = make_test_receipt()
    invalid["secret"] = "token-value"
    value = envelope(test_receipt=invalid)

    verdict = verify(value)

    assert verdict["decision"] == "UNKNOWN"
    assert verdict["reasons"] == ["tests-invalid"]
    assert "token-value" not in evidence._canonical(value)


def test_wrong_current_sha_diff_and_moved_base_stop_on_exact_coordinates():
    value = envelope()

    moved = verify(value, pr=pull_request(base_sha="e" * 40))
    wrong_head = verify(value, pr=pull_request(sha="e" * 40))
    wrong_diff = verify(value, diff_hash="e" * 64)

    assert moved["decision"] == "STOP"
    assert "coordinate-mismatch:base_sha" in moved["reasons"]
    assert wrong_head["decision"] == "STOP"
    assert "coordinate-mismatch:head_sha" in wrong_head["reasons"]
    assert wrong_diff["decision"] == "STOP"
    assert "coordinate-mismatch:diff_hash" in wrong_diff["reasons"]


def test_replay_and_stale_evidence_are_unknown_never_go():
    value = envelope()

    replay = verify(value, seen_envelope_ids=[value["envelope_id"]])
    stale = verify(value, now_ms=NOW + evidence.MAX_EVIDENCE_AGE_MS + 1)

    assert replay["decision"] == "UNKNOWN"
    assert replay["reasons"] == ["replay"]
    assert stale["decision"] == "UNKNOWN"
    assert stale["reasons"] == [
        "stale-envelope", "tests-stale", "ci-check_runs-stale",
        "ci-commit_statuses-stale",
    ]


def test_stale_test_receipt_and_invalid_replay_context_are_unknown(monkeypatch):
    monkeypatch.setattr(
        evidence,
        "_now_ms",
        lambda: NOW - evidence.MAX_EVIDENCE_AGE_MS - 1,
    )
    old_test = make_test_receipt()
    monkeypatch.setattr(evidence, "_now_ms", lambda: NOW)
    value = envelope(test_receipt=old_test)

    assert verify(value)["reasons"] == ["tests-stale"]
    invalid_replay = verify(value, seen_envelope_ids="not-a-digest-list")
    assert invalid_replay["decision"] == "UNKNOWN"
    assert invalid_replay["reasons"] == ["invalid-replay-context"]


def test_missing_or_invalid_ci_source_is_unknown_and_never_inferred():
    missing = verify(envelope(commit_statuses_receipt=None))
    invalid = make_ci_receipt("commit_statuses")
    invalid["extra"] = "unsupported"
    unsupported = verify(envelope(commit_statuses_receipt=invalid))

    assert missing["decision"] == "UNKNOWN"
    assert missing["reasons"] == ["ci-commit_statuses-unavailable"]
    assert unsupported["decision"] == "UNKNOWN"
    assert unsupported["reasons"] == ["ci-commit_statuses-unavailable"]


def test_failed_review_tests_or_ci_are_stop():
    blocked_proof = review_proof(quality="blocked")
    failed_tests = make_test_receipt(status="failed", result_digest="5" * 64)
    failing_ci = make_ci_receipt("check_runs", [Check("foundry", "completed", "failure")])

    assert verify(envelope(review_proof=blocked_proof))["decision"] == "STOP"
    assert verify(envelope(test_receipt=failed_tests))["decision"] == "STOP"
    assert verify(envelope(check_runs_receipt=failing_ci))["decision"] == "STOP"


def test_tampered_or_incomplete_envelope_is_unknown_not_an_exception():
    tampered = envelope()
    tampered["coordinates"]["head_sha"] = "e" * 40
    incomplete = copy.deepcopy(tampered)
    incomplete.pop("evidence")

    assert verify(tampered)["decision"] == "UNKNOWN"
    assert verify(incomplete)["decision"] == "UNKNOWN"


def test_claude_and_codex_facades_share_canonical_content_and_verdict():
    kwargs = {
        "repository": REPOSITORY,
        "issue_id": ISSUE,
        "ac_digest": AC_DIGEST,
        "pr": pull_request(),
        "diff_hash": DIFF,
        "review_proof": review_proof(),
        "test_receipt": make_test_receipt(),
        "check_runs_receipt": make_ci_receipt("check_runs"),
        "commit_statuses_receipt": make_ci_receipt("commit_statuses", []),
    }
    claude = evidence.claude_evidence_envelope(**kwargs)
    codex = evidence.codex_evidence_envelope(**kwargs)

    assert claude == codex
    verify_kwargs = {
        "repository": REPOSITORY,
        "issue_id": ISSUE,
        "ac_digest": AC_DIGEST,
        "pr": pull_request(),
        "diff_hash": DIFF,
        "now_ms": NOW,
    }
    assert evidence.claude_evidence_verdict(claude, **verify_kwargs) == (
        evidence.codex_evidence_verdict(codex, **verify_kwargs)
    )


@pytest.mark.parametrize(
    "forbidden",
    [
        {"token": "secret"},
        {"command": "pytest"},
        {"merge": True},
        {"deploy": "production"},
    ],
)
def test_test_receipt_schema_rejects_authority_and_secret_fields(forbidden):
    receipt = make_test_receipt()
    receipt.update(forbidden)

    value = envelope(test_receipt=receipt)

    assert value["evidence"]["tests"] == evidence._empty_tests("invalid")
    assert verify(value)["decision"] == "UNKNOWN"


def test_repository_identity_is_host_qualified_canonical_and_noncanonical_forms_fail_closed():
    assert evidence._repository(REPOSITORY) == REPOSITORY
    with pytest.raises(evidence.EvidencePlaneError):
        evidence._repository("patobiskoto/patolabs-plugins")
    with pytest.raises(evidence.EvidencePlaneError):
        evidence._repository("GitHub.com/Patobiskoto/Patolabs-Plugins")
    with pytest.raises(evidence.EvidencePlaneError):
        make_ci_receipt("check_runs", repository="patobiskoto/patolabs-plugins")
    with pytest.raises(evidence.EvidencePlaneError):
        make_test_receipt(repository="patobiskoto/patolabs-plugins")
    with pytest.raises(evidence.EvidencePlaneError):
        evidence.capture_ci_receipts(
            repository="gitlab.com/patobiskoto/patolabs-plugins",
            head_sha=HEAD,
        )


def test_public_ci_capture_reads_both_configured_sources_with_internal_coordinates_and_time(
    monkeypatch,
):
    class RecordingCodeHost:
        def __init__(self):
            self.calls = []

        def check_runs(self, repository, head_sha):
            self.calls.append(("check_runs", repository, head_sha))
            return [Check("required", "completed", "success")]

        def commit_statuses(self, repository, head_sha):
            self.calls.append(("commit_statuses", repository, head_sha))
            return [Check("legacy", "completed", "neutral")]

    codehost = RecordingCodeHost()
    observations = iter((NOW + 1, NOW + 2))
    monkeypatch.setattr(evidence, "_configured_codehost", lambda: codehost)
    monkeypatch.setattr(evidence, "_now_ms", lambda: next(observations))

    signature = inspect.signature(evidence.capture_ci_receipts)
    assert tuple(signature.parameters) == ("repository", "head_sha")
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert not hasattr(evidence, "create_ci_receipt")

    check_runs, commit_statuses = evidence.capture_ci_receipts(
        repository=REPOSITORY,
        head_sha=HEAD,
    )

    assert codehost.calls == [
        ("check_runs", "patobiskoto/patolabs-plugins", HEAD),
        ("commit_statuses", "patobiskoto/patolabs-plugins", HEAD),
    ]
    assert (check_runs["source"], commit_statuses["source"]) == (
        "check_runs", "commit_statuses",
    )
    assert (check_runs["repository"], commit_statuses["repository"]) == (
        REPOSITORY, REPOSITORY,
    )
    assert (check_runs["head_sha"], commit_statuses["head_sha"]) == (HEAD, HEAD)
    assert (check_runs["observed_at"], commit_statuses["observed_at"]) == (
        NOW + 1, NOW + 2,
    )
    assert check_runs["success"] == 1
    assert commit_statuses["neutral"] == 1


def test_manual_ci_receipt_recycling_is_unknown_and_wrong_coordinates_stop():
    check_runs = make_ci_receipt("check_runs")
    recycled = verify(envelope(commit_statuses_receipt=check_runs))

    assert recycled["decision"] == "UNKNOWN"
    assert recycled["reasons"] == ["ci-commit_statuses-unavailable"]

    wrong_sha = verify(envelope(check_runs_receipt=make_ci_receipt(
        "check_runs", head_sha="e" * 40,
    )))
    wrong_repository = verify(envelope(check_runs_receipt=make_ci_receipt(
        "check_runs", repository="github.com/other/repository",
    )))

    assert wrong_sha["decision"] == "STOP"
    assert "ci-coordinate-mismatch:check_runs:head_sha" in wrong_sha["reasons"]
    assert wrong_repository["decision"] == "STOP"
    assert "ci-coordinate-mismatch:check_runs:repository" in wrong_repository["reasons"]


def test_ci_receipts_bind_source_repository_head_and_freshness_independently():
    wrong_sha = verify(envelope(check_runs_receipt=make_ci_receipt(
        "check_runs", head_sha="e" * 40,
    )))
    assert wrong_sha["decision"] == "STOP"
    assert "ci-coordinate-mismatch:check_runs:head_sha" in wrong_sha["reasons"]

    wrong_repository = verify(envelope(check_runs_receipt=make_ci_receipt(
        "check_runs", repository="github.com/other/repository",
    )))
    assert wrong_repository["decision"] == "STOP"
    assert "ci-coordinate-mismatch:check_runs:repository" in wrong_repository["reasons"]

    stale = make_ci_receipt("check_runs", observed_at=NOW - evidence.MAX_EVIDENCE_AGE_MS - 1)
    future = make_ci_receipt("commit_statuses", [], observed_at=NOW + evidence.MAX_CLOCK_SKEW_MS + 1)
    verdict = verify(envelope(check_runs_receipt=stale, commit_statuses_receipt=future))
    assert verdict["decision"] == "UNKNOWN"
    assert "ci-check_runs-stale" in verdict["reasons"]
    assert "ci-commit_statuses-observation-in-future" in verdict["reasons"]


def test_ci_requires_two_closed_source_bound_receipts_and_rejects_swapping_or_tampering():
    assert verify(envelope(commit_statuses_receipt=None))["decision"] == "UNKNOWN"

    check_runs = make_ci_receipt("check_runs")
    statuses = make_ci_receipt("commit_statuses", [])
    swapped = verify(envelope(
        check_runs_receipt=statuses,
        commit_statuses_receipt=check_runs,
    ))
    assert swapped["decision"] == "UNKNOWN"
    assert "ci-check_runs-unavailable" in swapped["reasons"]
    assert "ci-commit_statuses-unavailable" in swapped["reasons"]

    tampered = make_ci_receipt("check_runs")
    tampered["extra"] = True
    assert verify(envelope(check_runs_receipt=tampered))["decision"] == "UNKNOWN"
