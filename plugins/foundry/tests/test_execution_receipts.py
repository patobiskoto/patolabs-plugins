import hashlib
import json
import stat
from types import SimpleNamespace

import pytest

from foundry import execution_receipts as receipts
from foundry import issue, write
from foundry.models import (
    EpicClosureChild,
    EpicClosureOutcome,
    EpicClosureReceipt,
    PullRequest,
)


HEAD = "a" * 40
BASE = "b" * 40
MERGE = "c" * 40
REPOSITORY = "patobiskoto/claude-plugins"
URL = "https://github.com/patobiskoto/claude-plugins/pull/91"
OBSERVED_AT = 1_788_299_000_000


def open_pr(**overrides):
    values = {
        "number": 91, "url": URL, "head": "feat/foundry-91", "base": "main",
        "base_sha": BASE, "sha": HEAD, "merged": False, "state": "open",
    }
    values.update(overrides)
    return PullRequest(**values)


def review_proof(**coordinate_overrides):
    coordinates = {"head": HEAD, "base": BASE, "diff_hash": "e" * 64}
    coordinates.update(coordinate_overrides)
    proof = {
        "schema_version": 1,
        "issue": {
            "id": "FOUNDRY-91", "ac_digest": "f" * 64,
            "criteria": [{"id": "ac-1", "digest": "1" * 64, "verdict": "pass"}],
        },
        "review": {"role": "reviewer", "generation": 1, "claim_digest": "2" * 64},
        "coordinates": coordinates,
        "quality": "mergeable",
    }
    proof["proof_id"] = hashlib.sha256(json.dumps(
        proof, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return proof


def closure_outcome():
    receipt = EpicClosureReceipt(
        project_key="FOUNDRY", project_id="project-1", parent_id="FOUNDRY-84",
        parent_version=7, parent_type="Epic", parent_ac_done=6, parent_ac_total=6,
        children=(
            EpicClosureChild("FOUNDRY-85", 4, "done"),
            EpicClosureChild("FOUNDRY-86", 5, "dropped"),
        ),
        issued_at=2_000_000, nonce="nonce_1234567890abcdef",
    )
    return EpicClosureOutcome(
        receipt=receipt, closed_parent_version=8, audit_id="audit:epic:84",
    )


def observed_pr(pr=None, *, operation="codehost.get_pr", observed_at=OBSERVED_AT):
    return receipts.pr_receipt(
        "github", REPOSITORY, pr or open_pr(), operation=operation,
        observed_at=observed_at,
    )


def test_structured_sources_project_exact_pr_review_ci_merge_and_epic_receipts():
    pr = open_pr()
    pr_observation = observed_pr(pr, operation="codehost.open_pr")
    assert pr_observation.receipt == {
        "provider": "github", "repository": REPOSITORY, "url": URL,
        "number": 91, "state": "open", "head_sha": HEAD, "base_sha": BASE,
    }
    assert pr_observation.source == {
        "operation": "codehost.open_pr", "provenance": "codehost",
        "adapter": "github",
        "adapter_version": receipts.SOURCE_ADAPTER_VERSION,
        "identity": {
            "provider": "github", "repository": REPOSITORY, "number": 91,
        },
        "observed_at": OBSERVED_AT,
    }
    review_observation = receipts.review_receipt(
        "github", REPOSITORY, pr, review_proof(), observed_at=OBSERVED_AT,
    )
    assert review_observation.receipt == {
        "provider": "github", "repository": REPOSITORY, "url": URL,
        "pr_number": 91, "verdict": "approved",
        "review_id": review_proof()["proof_id"],
        "reviewer": None, "head_sha": HEAD,
    }
    assert review_observation.source["operation"] == (
        "acceptance_proof.valid_for_merge"
    )
    ci_observation = receipts.ci_receipt("github", REPOSITORY, HEAD, {
        "passed": True, "waived": False, "pending": [], "failing": [], "total": 3,
        "reason": "ignored structured display text",
    }, observed_at=OBSERVED_AT)
    assert ci_observation.receipt == {
        "provider": "github", "repository": REPOSITORY, "url": None,
        "status": "success", "run_id": None, "head_sha": HEAD,
    }
    merged = open_pr(sha=MERGE, merged=True, merge_sha=MERGE, state="closed")
    merge_observation = receipts.merge_receipt(
        "github", REPOSITORY, 91, merged, MERGE,
        operation="codehost.merge_pr", observed_at=OBSERVED_AT,
    )
    assert merge_observation.receipt == {
        "provider": "github", "repository": REPOSITORY, "url": URL,
        "pr_number": 91, "merged": True, "merge_sha": MERGE,
    }
    epic_observation = receipts.epic_closure_receipt(
        "devhub", "FOUNDRY-84", closure_outcome(), observed_at=OBSERVED_AT,
    )
    assert epic_observation.receipt == {
        "epic_id": "FOUNDRY-84", "state": "closed",
        "receipt_id": "audit:epic:84", "parent_version": 8,
        "children": [
            {"issue_id": "FOUNDRY-85", "version": 4, "state": "done"},
            {"issue_id": "FOUNDRY-86", "version": 5, "state": "dropped"},
        ],
        "closure_digest": hashlib.sha256(json.dumps({
            "contract": receipts.EPIC_CLOSURE_COORDINATE_VERSION,
            "epic_id": "FOUNDRY-84", "state": "closed",
            "receipt_id": "audit:epic:84", "parent_version": 8,
            "children": [
                {"issue_id": "FOUNDRY-85", "version": 4, "state": "done"},
                {"issue_id": "FOUNDRY-86", "version": 5, "state": "dropped"},
            ],
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    assert epic_observation.source["identity"] == {
        "tracker": "devhub", "epic_id": "FOUNDRY-84",
    }


@pytest.mark.parametrize("waived", [False, True])
def test_no_ci_is_explicitly_unavailable_instead_of_inferred_success(waived):
    observation = receipts.ci_receipt("github", REPOSITORY, HEAD, {
        "passed": waived, "waived": waived, "pending": [], "failing": [], "total": 0,
    }, observed_at=OBSERVED_AT)
    assert observation.receipt is None
    assert observation.source["identity"]["head_sha"] == HEAD


@pytest.mark.parametrize(
    ("gate", "status"),
    [
        ({"passed": False, "waived": False, "pending": ["build"], "failing": [], "total": 1}, "in-progress"),
        ({"passed": False, "waived": False, "pending": [], "failing": ["test"], "total": 2}, "failure"),
        ({"passed": False, "waived": False, "pending": [], "failing": [], "total": 2}, "neutral"),
    ],
)
def test_ci_structured_non_green_paths_remain_explicit(gate, status):
    observation = receipts.ci_receipt(
        "github", REPOSITORY, HEAD, gate, observed_at=OBSERVED_AT,
    )
    assert observation.receipt["status"] == status


def test_unavailable_fields_and_whole_absent_receipts_stay_explicitly_null(tmp_path):
    observation = observed_pr(
        open_pr(url=None, sha=None, base_sha=None),
    )
    assert observation.receipt == {
        "provider": "github", "repository": REPOSITORY, "url": None,
        "number": 91, "state": "open", "head_sha": None, "base_sha": None,
    }
    store = receipts.ExecutionReceiptStore(tmp_path)
    no_ci = receipts.ci_receipt("github", REPOSITORY, HEAD, {
        "passed": False, "waived": False, "pending": [], "failing": [], "total": 0,
    }, observed_at=OBSERVED_AT)
    assert store.record("attempt-1", "FOUNDRY-91", "ci", no_ci) == "unavailable"
    assert store.receipts_for("attempt-1", "FOUNDRY-91") == {
        kind: None for kind in receipts.RECEIPT_KINDS
    }
    row = store.for_attempt("attempt-1")[0]
    assert row["receipt_states"]["ci"] == "unavailable"
    assert row["receipt_sources"]["ci"]["operation"] == "ci_gate.evaluate"


def test_unavailable_ci_enriches_same_attempt_when_source_becomes_available(tmp_path):
    store = receipts.ExecutionReceiptStore(tmp_path)
    unavailable = receipts.ci_receipt("github", REPOSITORY, HEAD, {
        "passed": False, "waived": False, "pending": [], "failing": [], "total": 0,
    }, observed_at=OBSERVED_AT)
    available = receipts.ci_receipt("github", REPOSITORY, HEAD, {
        "passed": True, "waived": False, "pending": [], "failing": [], "total": 1,
    }, observed_at=OBSERVED_AT + 1)

    assert store.record("attempt-1", "FOUNDRY-91", "ci", unavailable) == "unavailable"
    assert json.loads(store.path.read_text(encoding="utf-8"))["owners"] == {}
    assert store.record("attempt-1", "FOUNDRY-91", "ci", available) == "recorded"
    assert store.receipts_for("attempt-1", "FOUNDRY-91")["ci"]["status"] == "success"
    row = store.for_attempt("attempt-1")[0]
    assert row["receipt_states"]["ci"] == "available"
    assert row["receipt_rejections"]["ci"] is None
    assert row["receipt_sources"]["ci"]["observed_at"] == OBSERVED_AT + 1

    unavailable_again = receipts.ci_receipt("github", REPOSITORY, HEAD, {
        "passed": False, "waived": False, "pending": [], "failing": [], "total": 0,
    }, observed_at=OBSERVED_AT + 2)
    assert store.record(
        "attempt-1", "FOUNDRY-91", "ci", unavailable_again,
    ) == "unchanged"
    assert store.receipts_for("attempt-1", "FOUNDRY-91")["ci"]["status"] == "success"


def test_unavailable_ci_does_not_claim_or_steal_cross_attempt_ownership(tmp_path):
    store = receipts.ExecutionReceiptStore(tmp_path)
    unavailable = receipts.ci_receipt("github", REPOSITORY, HEAD, {
        "passed": False, "waived": False, "pending": [], "failing": [], "total": 0,
    }, observed_at=OBSERVED_AT)
    available = receipts.ci_receipt("github", REPOSITORY, HEAD, {
        "passed": True, "waived": False, "pending": [], "failing": [], "total": 1,
    }, observed_at=OBSERVED_AT + 1)

    assert store.record("attempt-1", "FOUNDRY-91", "ci", unavailable) == "unavailable"
    assert store.record("attempt-2", "FOUNDRY-91", "ci", available) == "recorded"
    assert store.record("attempt-1", "FOUNDRY-91", "ci", available) == "rejected"

    first = store.for_attempt("attempt-1")[0]
    second = store.for_attempt("attempt-2")[0]
    assert first["receipt_states"]["ci"] == "rejected"
    assert first["receipt_rejections"]["ci"] == "source-owned"
    assert first["receipts"]["ci"] is None
    assert second["receipt_states"]["ci"] == "available"
    assert second["receipt_rejections"]["ci"] is None
    assert second["receipts"]["ci"]["status"] == "success"


@pytest.mark.parametrize(
    "operation",
    [
        lambda: receipts.pr_receipt(
            "github", REPOSITORY, open_pr(sha="short"),
            operation="codehost.get_pr",
        ),
        lambda: receipts.review_receipt(
            "github", REPOSITORY, open_pr(), review_proof(head="9" * 40),
        ),
        lambda: receipts.ci_receipt("github", REPOSITORY, HEAD, {
            "passed": True, "waived": False, "pending": [], "failing": [], "total": 0,
        }),
        lambda: receipts.merge_receipt(
            "github", REPOSITORY, 91,
            open_pr(sha=MERGE, merged=True, merge_sha="9" * 40, state="closed"),
            MERGE, operation="codehost.merge_pr",
        ),
        lambda: receipts.epic_closure_receipt(
            "devhub", "FOUNDRY-91", closure_outcome(),
        ),
    ],
)
def test_incomplete_or_divergent_structured_sources_are_rejected(operation):
    with pytest.raises(receipts.ReceiptStoreError):
        operation()


def test_attempt_binding_is_idempotent_immutable_and_cross_attempt_exclusive(tmp_path):
    store = receipts.ExecutionReceiptStore(tmp_path)
    first = observed_pr(operation="codehost.open_pr")
    changed = observed_pr(
        open_pr(sha="9" * 40), observed_at=OBSERVED_AT + 1,
    )

    assert store.record("attempt-1", "FOUNDRY-91", "pr", first) == "recorded"
    assert store.record("attempt-1", "FOUNDRY-91", "pr", first) == "unchanged"
    assert store.record("attempt-1", "FOUNDRY-91", "pr", changed) == "rejected"
    assert store.receipts_for("attempt-1", "FOUNDRY-91")["pr"] is None

    evolved = observed_pr(
        open_pr(state="closed", sha="8" * 40, url=f"{URL}/"),
        observed_at=OBSERVED_AT + 2,
    )
    assert store.record("attempt-2", "FOUNDRY-92", "pr", evolved) == "rejected"
    assert store.receipts_for("attempt-2", "FOUNDRY-92")["pr"] is None
    first_row = store.for_attempt("attempt-1")[0]
    second_row = store.for_attempt("attempt-2")[0]
    assert first_row["receipt_rejections"]["pr"] == "divergent-observation"
    assert second_row["receipt_states"]["pr"] == "rejected"
    assert second_row["receipt_rejections"]["pr"] == "source-owned"
    assert second_row["receipt_sources"]["pr"]["identity"] == {
        "provider": "github", "repository": REPOSITORY, "number": 91,
    }


def test_publisher_seam_lists_only_exact_attempt_issue_bindings(tmp_path):
    store = receipts.ExecutionReceiptStore(tmp_path)
    store.record(
        "attempt-1", "FOUNDRY-91", "pr",
        observed_pr(operation="codehost.open_pr"),
    )
    store.record(
        "attempt-1", "FOUNDRY-84", "epic_closure",
        receipts.epic_closure_receipt(
            "devhub", "FOUNDRY-84", closure_outcome(),
            observed_at=OBSERVED_AT,
        ),
    )
    store.record(
        "attempt-2", "FOUNDRY-92", "ci",
        receipts.ci_receipt("github", REPOSITORY, HEAD, {
            "passed": True, "waived": False, "pending": [], "failing": [], "total": 1,
        }, observed_at=OBSERVED_AT),
    )

    rows = store.for_attempt("attempt-1")
    assert [row["issue_id"] for row in rows] == ["FOUNDRY-84", "FOUNDRY-91"]
    assert all(row["attempt_id"] == "attempt-1" for row in rows)
    assert rows[0]["receipts"]["epic_closure"]["receipt_id"] == "audit:epic:84"
    assert rows[1]["receipts"]["pr"]["number"] == 91
    assert rows[1]["receipt_states"]["pr"] == "available"
    assert rows[1]["receipt_rejections"]["pr"] is None
    assert rows[1]["receipt_sources"]["pr"]["observed_at"] == OBSERVED_AT


def test_closed_schema_refuses_sensitive_payloads_and_private_store_contains_none(tmp_path):
    store = receipts.ExecutionReceiptStore(tmp_path)
    safe = observed_pr()
    unsafe = receipts.ReceiptObservation(
        kind="pr", source=safe.source,
        receipt={
            **safe.receipt, "token": "super-secret",
            "prompt": "private-agent-output",
        },
    )
    with pytest.raises(receipts.ReceiptStoreError, match="schema"):
        store.record("attempt-1", "FOUNDRY-91", "pr", unsafe)
    unsafe_source = receipts.ReceiptObservation(
        kind="pr",
        source={**safe.source, "raw_payload": "private-provider-payload"},
        receipt=safe.receipt,
    )
    with pytest.raises(receipts.ReceiptStoreError, match="schema"):
        store.record("attempt-1", "FOUNDRY-91", "pr", unsafe_source)
    assert not store.path.exists()

    store.record("attempt-1", "FOUNDRY-91", "pr", safe)
    payload = store.path.read_text(encoding="utf-8")
    assert "super-secret" not in payload
    assert "private-agent-output" not in payload
    assert "private-provider-payload" not in payload
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    decoded = json.loads(payload)
    assert set(decoded) == {"schema", "bindings", "owners"}

    binding = next(iter(decoded["bindings"].values()))
    binding["receipts"]["pr"]["token"] = "injected-secret"
    store.path.write_text(json.dumps(decoded), encoding="utf-8")
    with pytest.raises(receipts.ReceiptStoreError, match="schema"):
        store.receipts_for("attempt-1", "FOUNDRY-91")


@pytest.mark.parametrize("url", [
    "https://credential-marker@github.com/patobiskoto/claude-plugins/pull/91",
    "https://github.com/patobiskoto/claude-plugins/pull/91?token=credential-marker",
    "https://github.com/patobiskoto/claude-plugins/pull/91#credential-marker",
    "https://github.com/patobiskoto/claude-plugins/pull/91;token=credential-marker",
])
def test_credential_bearing_urls_are_rejected_before_persistence(tmp_path, url):
    with pytest.raises(receipts.ReceiptStoreError, match="invalid_url"):
        observation = observed_pr(open_pr(url=url))
        receipts.ExecutionReceiptStore(tmp_path).record(
            "attempt-1", "FOUNDRY-91", "pr", observation,
        )
    assert not (tmp_path / "receipts.json").exists()


def test_environment_observation_is_noop_without_attempt_and_passive_on_bad_source(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv(receipts.ATTEMPT_ID_ENV, raising=False)
    assert receipts.observe("FOUNDRY-91", "pr", {"token": "secret"}) == "unavailable"

    monkeypatch.setenv(receipts.ATTEMPT_ID_ENV, "attempt-1")
    monkeypatch.setenv(receipts.RECEIPT_DIRECTORY_ENV, str(tmp_path))
    assert receipts.observe("FOUNDRY-91", "pr", {"token": "secret"}) == "rejected"
    assert not (tmp_path / "receipts.json").exists()


def _bind_attempt(monkeypatch, tmp_path, attempt="attempt-1"):
    monkeypatch.setenv(receipts.ATTEMPT_ID_ENV, attempt)
    monkeypatch.setenv(receipts.RECEIPT_DIRECTORY_ENV, str(tmp_path))


def test_openpr_operation_records_only_its_structured_codehost_result(
    tmp_path, monkeypatch,
):
    _bind_attempt(monkeypatch, tmp_path)
    tracker = SimpleNamespace(
        bounded_transition_proofs=False,
        get_issue=lambda _issue: SimpleNamespace(title="Receipts", type="Feature"),
    )
    codehost = SimpleNamespace(
        name="github", resolve_repo=lambda: REPOSITORY, list_prs=lambda *_args: [],
        open_pr=lambda *_args: open_pr(),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(
        issue, "_sh",
        lambda *args, **_kwargs: "feat/foundry-91-receipts"
        if args[-2:] == ("--abbrev-ref", "HEAD") else "",
    )
    monkeypatch.setattr(write, "issue_binding", lambda *_args: None)
    monkeypatch.setattr(write, "transition", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(write, "set_field", lambda *_args, **_kwargs: None)

    issue.openpr("FOUNDRY-91")

    projected = receipts.ExecutionReceiptStore(tmp_path).receipts_for(
        "attempt-1", "FOUNDRY-91",
    )
    assert projected["pr"]["head_sha"] == HEAD
    assert all(projected[kind] is None for kind in receipts.RECEIPT_KINDS if kind != "pr")
    row = receipts.ExecutionReceiptStore(tmp_path).for_attempt("attempt-1")[0]
    assert row["receipt_sources"]["pr"]["operation"] == "codehost.open_pr"


def test_merge_operation_collects_review_ci_and_merge_without_changing_gates(
    tmp_path, monkeypatch,
):
    _bind_attempt(monkeypatch, tmp_path)
    current = SimpleNamespace(
        id="FOUNDRY-91", state="review", body="- [ ] proof", ac_done=0, ac_total=1,
    )
    tracker = SimpleNamespace(
        bounded_transition_proofs=False, get_issue=lambda _issue: current,
    )
    original = open_pr()
    merged = open_pr(sha=MERGE, merge_sha=MERGE, merged=True, state="closed")
    calls = []
    codehost = SimpleNamespace(
        name="github", resolve_repo=lambda: REPOSITORY,
        get_pr=lambda *_args: original,
        merge_pr=lambda *_args, **_kwargs: calls.append("merge") or merged,
        delete_branch=lambda *_args: calls.append("delete"),
    )
    proof = review_proof()
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(write, "issue_binding", lambda *_args: None)
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: {
        "passed": True, "waived": False, "pending": [], "failing": [], "total": 2,
        "reason": "tous verts",
    })
    monkeypatch.setattr(write, "sync_acceptance", lambda *_args, **_kwargs: {
        "status": "updated", "checked": 1,
    })
    monkeypatch.setattr(write, "add_comment", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(write, "transition", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(issue, "git_head", lambda: HEAD)
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: b"reviewed diff")
    monkeypatch.setattr(issue, "repository_identity", lambda: REPOSITORY)
    monkeypatch.setattr(
        issue, "AcceptanceProofStore",
        lambda _repository: SimpleNamespace(valid_for_merge=lambda **_kwargs: proof),
    )
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")

    issue.merge("FOUNDRY-91", "91")

    assert calls == ["merge", "delete"]
    projected = receipts.ExecutionReceiptStore(tmp_path).receipts_for(
        "attempt-1", "FOUNDRY-91",
    )
    assert projected["pr"]["number"] == 91
    assert projected["review"]["review_id"] == proof["proof_id"]
    assert projected["ci"] == {
        "provider": "github", "repository": REPOSITORY, "url": None,
        "status": "success", "run_id": None, "head_sha": HEAD,
    }
    assert projected["merge"]["merge_sha"] == MERGE
    assert projected["epic_closure"] is None
    row = receipts.ExecutionReceiptStore(tmp_path).for_attempt("attempt-1")[0]
    assert row["receipt_sources"]["merge"]["operation"] == "codehost.merge_pr"


def test_done_issue_resume_records_existing_structured_merge_proof(
    tmp_path, monkeypatch,
):
    _bind_attempt(monkeypatch, tmp_path)
    current = SimpleNamespace(id="FOUNDRY-91", state="done", pr_url=URL)
    tracker = SimpleNamespace(
        bounded_transition_proofs=True, get_issue=lambda _issue: current,
    )
    merged = open_pr(merged=True, merge_sha=MERGE, state="closed")
    calls = []
    codehost = SimpleNamespace(
        name="github", resolve_repo=lambda: REPOSITORY,
        get_pr=lambda *_args: merged,
        delete_branch=lambda *_args: calls.append("delete"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(write, "issue_binding", lambda *_args: None)
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")

    issue.merge("FOUNDRY-91", "91")

    assert calls == ["delete"]
    store = receipts.ExecutionReceiptStore(tmp_path)
    projected = store.receipts_for("attempt-1", "FOUNDRY-91")
    assert projected["pr"]["state"] == "merged"
    assert projected["merge"] == {
        "provider": "github", "repository": REPOSITORY, "url": URL,
        "pr_number": 91, "merged": True, "merge_sha": MERGE,
    }
    row = store.for_attempt("attempt-1")[0]
    assert row["receipt_states"]["merge"] == "available"
    assert row["receipt_sources"]["merge"]["operation"] == "codehost.get_pr"


def test_failed_ci_operation_still_refuses_merge_and_records_only_the_gate_fact(
    tmp_path, monkeypatch,
):
    _bind_attempt(monkeypatch, tmp_path)
    tracker = SimpleNamespace(
        bounded_transition_proofs=False,
        get_issue=lambda _issue: SimpleNamespace(
            id="FOUNDRY-91", state="review", body="", ac_done=1, ac_total=1,
        ),
    )
    codehost = SimpleNamespace(
        name="github", resolve_repo=lambda: REPOSITORY, get_pr=lambda *_args: open_pr(),
        merge_pr=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("merge")),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(write, "issue_binding", lambda *_args: None)
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: {
        "passed": False, "waived": False, "pending": [], "failing": ["tests"],
        "total": 1, "reason": "checks en échec",
    })

    with pytest.raises(SystemExit, match="CI non verte"):
        issue.merge("FOUNDRY-91", "91")

    projected = receipts.ExecutionReceiptStore(tmp_path).receipts_for(
        "attempt-1", "FOUNDRY-91",
    )
    assert projected["ci"]["status"] == "failure"
    assert projected["merge"] is None


def test_close_epic_operation_records_tracker_audit_without_affecting_completion(
    tmp_path, monkeypatch,
):
    _bind_attempt(monkeypatch, tmp_path)
    outcome = closure_outcome()
    monkeypatch.setattr(issue.foundry, "tracker", lambda: SimpleNamespace(name="devhub"))
    monkeypatch.setattr(write, "close_epic", lambda *_args, **_kwargs: outcome)

    issue.close_epic("FOUNDRY-84")

    projected = receipts.ExecutionReceiptStore(tmp_path).receipts_for(
        "attempt-1", "FOUNDRY-84",
    )
    assert projected["epic_closure"] == {
        "epic_id": "FOUNDRY-84", "state": "closed",
        "receipt_id": "audit:epic:84", "parent_version": 8,
        "children": [
            {"issue_id": "FOUNDRY-85", "version": 4, "state": "done"},
            {"issue_id": "FOUNDRY-86", "version": 5, "state": "dropped"},
        ],
        "closure_digest": receipts.epic_closure_receipt(
            "devhub", "FOUNDRY-84", outcome,
        ).receipt["closure_digest"],
    }
    row = receipts.ExecutionReceiptStore(tmp_path).for_attempt("attempt-1")[0]
    assert row["receipt_sources"]["epic_closure"]["adapter"] == "devhub"
