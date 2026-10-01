from __future__ import annotations

import hashlib
import urllib.parse

import pytest

from foundry import write
from foundry.models import Issue, MergeDeliveryReceipt, Project
from foundry.routing import acceptance_criteria, acceptance_digest
from foundry.trackers.base import TrackerConflictError
from foundry.trackers.youtrack import YouTrackTracker


PROJECT = Project(
    "APP", "project-app",
    {"ms_bundle": "bundle-app", "release_ids": {"v1": "release-v1"}},
)
PR_URL = "https://github.com/acme/app/pull/7"
BODY = "- [x] exact acceptance\n"


def _receipt(*, acceptance="accepted", project=PROJECT, body=BODY):
    return MergeDeliveryReceipt(
        project_key=project.key,
        project_id=project.id,
        issue_id="APP-7",
        codehost="github",
        repository="acme/app",
        pr_number=7,
        pr_url=PR_URL,
        head_sha="a" * 40,
        base_sha="b" * 40,
        review_digest="c" * 64,
        merge_sha="d" * 40,
        body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        ac_digest=acceptance_digest(acceptance_criteria(body)),
        review_proof_id="e" * 64,
        review_generation=1,
        acceptance=acceptance,
        acceptance_source=(
            "structured-review-proof" if acceptance == "accepted" else "human-override"
        ),
        override_reason=None if acceptance == "accepted" else "approved-exception",
    )


def _issue(*, body=BODY, state="Done", pr_url=PR_URL, comments=()):
    done, total = YouTrackTracker._ac_counts(body)
    return Issue(
        "APP-7", "delivery", state=state, native_state=state,
        body=body, pr_url=pr_url, ac_done=done, ac_total=total,
        comments=list(comments),
    )


@pytest.mark.parametrize(
    ("acceptance", "expected"),
    [("accepted", "accepted"), ("deviated", "deviated")],
)
def test_exact_delivery_receipt_classifies_only_its_qualified_disposition(
    acceptance, expected,
):
    receipt = _receipt(acceptance=acceptance)
    _marker, text = YouTrackTracker._delivery_audit(receipt)
    issue = _issue(comments=[{"id": "comment-1", "text": text}])

    observed = YouTrackTracker._delivery_from_issue(
        issue, PROJECT, require_done=True,
    )

    assert observed is not None
    assert observed.acceptance == expected


@pytest.mark.parametrize(
    "mutation",
    ["altered", "duplicate", "foreign", "body-drift", "pr-drift", "native-reopen"],
)
def test_altered_duplicate_foreign_or_native_drift_never_qualifies(mutation):
    receipt = _receipt()
    if mutation == "foreign":
        receipt = _receipt(project=Project("FOREIGN", "foreign-project"))
    _marker, text = YouTrackTracker._delivery_audit(receipt)
    if mutation == "altered":
        text += " "
    comments = [{"id": "comment-1", "text": text}]
    if mutation == "duplicate":
        comments.append({"id": "comment-2", "text": text})
    issue = _issue(
        body="- [x] changed acceptance\n" if mutation == "body-drift" else BODY,
        pr_url="https://github.com/acme/app/pull/8" if mutation == "pr-drift" else PR_URL,
        state="Review" if mutation == "native-reopen" else "Done",
        comments=comments,
    )

    with pytest.raises(TrackerConflictError):
        YouTrackTracker._delivery_from_issue(issue, PROJECT, require_done=True)


def test_free_text_note_never_becomes_delivery_authority():
    issue = _issue(comments=[{
        "id": "comment-1",
        "text": "Merged and accepted by a human; sha " + "d" * 40,
    }])

    assert YouTrackTracker._delivery_from_issue(
        issue, PROJECT, require_done=True,
    ) is None


def test_comment_read_exhausts_top_skip_pages():
    tracker = object.__new__(YouTrackTracker)
    skips = []

    def request(method, path, body=None, fields=None, top=None):
        del body, fields, top
        assert method == "GET"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        skip = int(query["$skip"][0])
        skips.append(skip)
        if skip == 0:
            return [
                {"id": "c1", "text": "one"},
                {"id": "c2", "text": "two"},
            ]
        return [{"id": "c3", "text": "three"}]

    tracker._req = request

    assert [row["id"] for row in tracker._issue_comments("APP-7", 2)] == [
        "c1", "c2", "c3",
    ]
    assert skips == [0, 2]


class _AppendTracker(YouTrackTracker):
    def __init__(self, *, visible_on_loss: bool, state: str = "Review"):
        self.visible_on_loss = visible_on_loss
        self.state = state
        self.comments = []
        self.appends = 0

    def validate_issue_binding(self, project, *issue_ids):
        assert project == PROJECT and issue_ids == ("APP-7",)

    def get_issue(self, issue_id):
        assert issue_id == "APP-7"
        return _issue(state=self.state)

    def _issue_comments(self, issue_id, page_size=100):
        assert issue_id == "APP-7"
        del page_size
        return list(self.comments)

    def add_comment(self, issue_id, text, project=None):
        assert issue_id == "APP-7" and project == PROJECT
        self.appends += 1
        if self.visible_on_loss:
            self.comments.append({"id": "delivery-1", "text": text})
        raise OSError("response lost")


class _NominalAppendTracker(_AppendTracker):
    def add_comment(self, issue_id, text, project=None):
        assert issue_id == "APP-7" and project == PROJECT
        self.appends += 1
        self.comments.append({"id": "delivery-1", "text": text})


def _record_via_write(tracker, receipt):
    return write.record_delivery_receipt(
        tracker,
        receipt.issue_id,
        project=PROJECT,
        codehost=receipt.codehost,
        repository=receipt.repository,
        pr_number=receipt.pr_number,
        pr_url=receipt.pr_url,
        head_sha=receipt.head_sha,
        base_sha=receipt.base_sha,
        review_digest=receipt.review_digest,
        merge_sha=receipt.merge_sha,
        acceptance=receipt.acceptance,
        review_proof_id=receipt.review_proof_id,
        review_generation=receipt.review_generation,
        proof_ac_digest=receipt.ac_digest,
    )


def test_historical_done_without_receipt_refuses_backfill_before_intent(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    tracker = _AppendTracker(visible_on_loss=True, state="Done")
    receipt = _receipt()

    with pytest.raises(TrackerConflictError, match="backfill refusé"):
        _record_via_write(tracker, receipt)

    fingerprint = tracker._delivery_scope_fingerprint(PROJECT, receipt.issue_id)
    assert tracker.appends == 0
    assert tracker._read_delivery_intent(fingerprint) is None


def test_done_with_exact_existing_receipt_replay_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    tracker = _AppendTracker(visible_on_loss=True, state="Done")
    receipt = _receipt()
    _marker, audit = tracker._delivery_audit(receipt)
    tracker.comments.append({"id": "delivery-1", "text": audit})

    assert _record_via_write(tracker, receipt) is False
    assert tracker.appends == 0


def test_fresh_review_nominally_appends_one_receipt(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    tracker = _NominalAppendTracker(visible_on_loss=True)
    receipt = _receipt()

    assert _record_via_write(tracker, receipt) is True
    assert tracker.appends == 1
    intent = tracker._read_delivery_intent(
        tracker._delivery_scope_fingerprint(PROJECT, receipt.issue_id)
    )
    assert intent is not None and intent["state"] == "complete"


def test_lost_response_converges_by_exact_readback_without_second_append(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    tracker = _AppendTracker(visible_on_loss=True)
    receipt = _receipt()

    assert tracker.record_delivery_receipt(PROJECT, receipt) is True
    assert tracker.record_delivery_receipt(PROJECT, receipt) is False
    assert tracker.appends == 1
    intent = tracker._read_delivery_intent(
        tracker._delivery_scope_fingerprint(PROJECT, "APP-7")
    )
    assert intent is not None and intent["state"] == "complete"


def test_invisible_lost_response_persists_intent_and_never_posts_again(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    tracker = _AppendTracker(visible_on_loss=False)
    receipt = _receipt()

    with pytest.raises(TrackerConflictError, match="no second POST"):
        tracker.record_delivery_receipt(PROJECT, receipt)
    with pytest.raises(TrackerConflictError, match="no second POST"):
        tracker.record_delivery_receipt(PROJECT, receipt)
    assert tracker.appends == 1
    intent = tracker._read_delivery_intent(
        tracker._delivery_scope_fingerprint(PROJECT, "APP-7")
    )
    assert intent is not None and intent["state"] == "pending"


def test_state_drift_before_first_append_refuses_without_post(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    tracker = _AppendTracker(visible_on_loss=True, state="Blocked")

    with pytest.raises(TrackerConflictError, match="projection native"):
        tracker.record_delivery_receipt(PROJECT, _receipt())
    assert tracker.appends == 0


def test_ac_drift_after_review_proof_refuses_before_adapter_append():
    class Drifted:
        delivery_receipt_supported = True

        def __init__(self):
            self.appended = False

        def validate_issue_binding(self, project, issue_id):
            assert project == PROJECT and issue_id == "APP-7"

        def get_issue(self, issue_id):
            assert issue_id == "APP-7"
            return _issue(body="- [x] changed after review\n", state="Review")

        def record_delivery_receipt(self, project, receipt):
            self.appended = True
            raise AssertionError((project, receipt))

    tracker = Drifted()
    with pytest.raises(TrackerConflictError, match="AC modifiées depuis la preuve"):
        write.record_delivery_receipt(
            tracker,
            "APP-7",
            project=PROJECT,
            codehost="github",
            repository="acme/app",
            pr_number=7,
            pr_url=PR_URL,
            head_sha="a" * 40,
            base_sha="b" * 40,
            review_digest="c" * 64,
            merge_sha="d" * 40,
            acceptance="accepted",
            review_proof_id="e" * 64,
            review_generation=1,
            proof_ac_digest=acceptance_digest(acceptance_criteria(BODY)),
        )
    assert tracker.appended is False
