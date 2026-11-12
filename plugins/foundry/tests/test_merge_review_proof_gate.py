"""PLP-368 / PLP-ADR-0064: on every provider, a code merge needs a completed review proof.

Each case drives the real ``issue.merge`` with the capability flags of a real adapter class
(DevHub, Linear, GitHub Projects, YouTrack), a real temporary Git repository and the real
``AcceptanceProofStore`` under the isolated test home. Tracker writes, the CI gate and the
code host are local recorders: no network, no tracker, no user configuration.
"""
import subprocess
from types import SimpleNamespace

import pytest

from foundry import issue, registry, write
from foundry.models import Project
from foundry.routing import (
    AcceptanceProofStore,
    ReviewDeduplicator,
    acceptance_criteria,
    git_diff,
    review_diff_hash,
)
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker

REPOSITORY = "acme/demo"
ISSUE = "DEMO-7"
PR_URL = "https://github.com/acme/demo/pull/12"
TOKEN = "c" * 64
CHECKED = "- [x] first criterion\n- [x] second criterion\n"
PARTIAL = "- [x] first criterion\n- [ ] second criterion\n"
OVERRIDE = {"--allow-incomplete-ac", "--ac-override-reason=human_confirmed"}
PROVIDERS = [DevHubTracker, LinearTracker, GitHubProjectsTracker, YouTrackTracker]
TYPED_RECEIPT = {DevHubTracker, LinearTracker}


def _git(root, *args):
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout.strip()


def _commit(root, name, text):
    (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", name)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A PR branch one commit ahead of its base; the cwd, as for a real ``issue merge``."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    base = _commit(root, "a.txt", "base\n")
    _git(root, "checkout", "-q", "-b", "feat/demo-7")
    head = _commit(root, "a.txt", "reviewed change\n")
    monkeypatch.chdir(root)
    return SimpleNamespace(root=root, base=base, head=head)


def _record_proof(repo, *, quality, verdicts):
    """Record a completed review proof through the same claim and store as the reviewer."""
    diff_hash = review_diff_hash(git_diff(repo.root, repo.base))
    claim = ReviewDeduplicator(REPOSITORY).claim_git(
        diff_hash, coordinates={"root": str(repo.root.resolve()), "base": repo.base},
        claim_attempt_token=TOKEN,
    )
    outcomes = [
        {**item, "verdict": verdict}
        for item, verdict in zip(acceptance_criteria(CHECKED), verdicts, strict=True)
    ]
    return AcceptanceProofStore(REPOSITORY).create(
        issue_id=ISSUE, issue_body=CHECKED, reviewer_role="reviewer", outcomes=outcomes,
        quality=quality, diff_hash=diff_hash, claim_id=claim.claim_id,
        root=repo.root, base=repo.base,
    )


def _world(monkeypatch, provider, repo, *, body, binding=Project("DEMO", "project-demo")):
    events = []
    current = SimpleNamespace(
        id=ISSUE, state="review", normalized_state="review", pr_url=PR_URL, body=body,
        ac_done=body.count("[x]"), ac_total=2,
    )
    tracker = object.__new__(provider)
    tracker.get_issue = lambda _issue_id: current
    pr = SimpleNamespace(
        number=12, url=PR_URL, sha=repo.head, base_sha=repo.base, head="feat/demo-7",
        base="main", state="open", merged=False, merge_sha=None,
    )
    landed = SimpleNamespace(sha="b" * 40, head=pr.head, merged=True)
    codehost = SimpleNamespace(
        name="github", resolve_repo=lambda: REPOSITORY, get_pr=lambda *_args: pr,
        merge_pr=lambda *_args, **_kwargs: events.append(("merge",)) or landed,
        delete_branch=lambda *_args: events.append(("delete",)),
    )

    def sync_acceptance(_tracker, _issue_id, _body, proof):
        current.ac_done = current.ac_total
        events.append(("sync", proof["proof_id"]))
        return {"status": "updated", "checked": 1}

    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "repository_identity", lambda: REPOSITORY)
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_args: None)
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")
    monkeypatch.setattr(write, "issue_binding", lambda *_args, **_kwargs: binding)
    monkeypatch.setattr(write, "preflight_issue_operation", lambda *_args: None)
    monkeypatch.setattr(write, "preflight_merge_effect", lambda *_args: None)
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: {
        "passed": True, "waived": False, "total": 1, "pending": [], "failing": [],
    })
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, _issue_id, state, context=None: events.append(("transition", state)),
    )
    monkeypatch.setattr(
        write, "add_comment",
        lambda _tracker, _issue_id, text, **_kwargs: events.append(("comment", text)),
    )
    monkeypatch.setattr(
        write, "project_acceptance_override",
        lambda _tracker, _issue_id, reason, _context: events.append(
            ("override-receipt", reason)) or True,
    )
    monkeypatch.setattr(write, "sync_acceptance", sync_acceptance)
    monkeypatch.setattr(
        write, "record_delivery_receipt",
        lambda *_args, **kwargs: events.append(
            ("delivery", kwargs["acceptance"], kwargs["review_proof_id"])) or True,
    )
    return events


def _names(events):
    return [event[0] for event in events]


def _no_effect(events):
    names = _names(events)
    assert "merge" not in names and "delete" not in names
    assert "comment" not in names and "override-receipt" not in names
    assert ("transition", "done") not in events


@pytest.mark.parametrize("provider", PROVIDERS)
def test_checked_criteria_without_any_proof_are_refused(monkeypatch, repo, provider):
    events = _world(monkeypatch, provider, repo, body=CHECKED)

    with pytest.raises(SystemExit, match="AC tracker cochées mais aucune preuve de review "
                                         "terminée.*aucune preuve AC disponible"):
        issue.merge(ISSUE, "12")

    _no_effect(events)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_checked_criteria_with_a_mergeable_all_pass_proof_merge(monkeypatch, repo, provider):
    proof = _record_proof(repo, quality="mergeable", verdicts=["pass", "pass"])
    events = _world(monkeypatch, provider, repo, body=CHECKED)

    issue.merge(ISSUE, "12")

    names = _names(events)
    assert "merge" in names and ("transition", "done") in events
    assert "sync" not in names and "override-receipt" not in names and "comment" not in names
    if provider is YouTrackTracker:
        assert ("delivery", "accepted", proof["proof_id"]) in events


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(("quality", "verdicts"), [
    ("mergeable", ["pass", "fail"]),
    ("mergeable", ["pass", "not_covered"]),
    ("blocked", ["pass", "pass"]),
])
def test_checked_criteria_with_a_proof_that_is_not_all_pass_point_to_the_override(
    monkeypatch, repo, provider, quality, verdicts,
):
    _record_proof(repo, quality=quality, verdicts=verdicts)
    events = _world(monkeypatch, provider, repo, body=CHECKED)

    with pytest.raises(SystemExit) as refused:
        issue.merge(ISSUE, "12")

    message = str(refused.value.code)
    assert message.startswith("⛔ Merge refusé — AC tracker cochées")
    assert "non mergeable ou incomplète" in message
    assert "--allow-incomplete-ac --ac-override-reason=<code>" in message
    _no_effect(events)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("body", [CHECKED, PARTIAL], ids=["checked", "partial"])
def test_override_without_any_proof_is_refused(monkeypatch, repo, provider, body):
    events = _world(monkeypatch, provider, repo, body=body)

    with pytest.raises(SystemExit, match="Override AC refusé — aucune preuve de review "
                                         "terminée.*aucune preuve AC disponible"):
        issue.merge(ISSUE, "12", flags=OVERRIDE)

    _no_effect(events)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    "body, flags",
    [(CHECKED, set()), (PARTIAL, set()), (CHECKED, OVERRIDE), (PARTIAL, OVERRIDE)],
    ids=["checked", "partial", "checked-override", "partial-override"],
)
def test_the_refusal_for_lack_of_proof_names_the_data_directory_read(
    monkeypatch, repo, provider, body, flags,
):
    events = _world(monkeypatch, provider, repo, body=body)

    with pytest.raises(SystemExit) as refusal:
        issue.merge(ISSUE, "12", flags=flags)

    assert f"Dossier de données lu : {registry.data_dir()}" in str(refusal.value)
    _no_effect(events)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_override_audit_text_does_not_say_incomplete_when_every_box_is_checked(
    monkeypatch, repo, provider,
):
    proof = _record_proof(repo, quality="blocked", verdicts=["pass", "fail"])
    events = _world(monkeypatch, provider, repo, body=CHECKED)

    issue.merge(ISSUE, "12", flags=OVERRIDE)

    _assert_override_trace_before_merge(events, provider, proof)
    texts = [event[1] for event in events if event[0] == "comment"]
    assert all("AC incomplètes" not in text for text in texts)
    assert any("de la preuve de review non mergeable ou non toute pass" in text for text in texts)


def _assert_override_trace_before_merge(events, provider, proof):
    names = _names(events)
    trace = "override-receipt" if provider in TYPED_RECEIPT else "comment"
    assert trace in names and names.index(trace) < names.index("merge")
    if trace == "override-receipt":
        assert ("override-receipt", "human_confirmed") in events
    else:
        assert any("human_confirmed" in event[1] for event in events if event[0] == "comment")
    assert "sync" not in names
    if provider is YouTrackTracker:
        assert ("delivery", "deviated", proof["proof_id"]) in events


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("body", [CHECKED, PARTIAL], ids=["checked", "partial"])
def test_override_with_a_completed_blocked_proof_writes_its_trace_before_the_merge(
    monkeypatch, repo, provider, body,
):
    proof = _record_proof(repo, quality="blocked", verdicts=["pass", "fail"])
    events = _world(monkeypatch, provider, repo, body=body)

    issue.merge(ISSUE, "12", flags=OVERRIDE)

    _assert_override_trace_before_merge(events, provider, proof)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_override_with_a_mergeable_proof_that_is_not_all_pass_is_allowed(
    monkeypatch, repo, provider,
):
    proof = _record_proof(repo, quality="mergeable", verdicts=["pass", "not_covered"])
    events = _world(monkeypatch, provider, repo, body=PARTIAL)

    issue.merge(ISSUE, "12", flags=OVERRIDE)

    _assert_override_trace_before_merge(events, provider, proof)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_unchecked_criteria_with_a_valid_proof_still_sync_then_merge(monkeypatch, repo, provider):
    """Non-regression: today's proof-backed merge of lagging checkboxes is unchanged."""
    proof = _record_proof(repo, quality="mergeable", verdicts=["pass", "pass"])
    events = _world(monkeypatch, provider, repo, body=PARTIAL)

    issue.merge(ISSUE, "12")

    names = _names(events)
    assert ("sync", proof["proof_id"]) in events
    assert names.index("sync") < names.index("merge")
    assert "override-receipt" not in names
    if provider is YouTrackTracker:
        assert ("delivery", "accepted", proof["proof_id"]) in events


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_proof_of_a_previous_head_is_not_seen(monkeypatch, repo, provider):
    _record_proof(repo, quality="blocked", verdicts=["pass", "pass"])
    repo.head = _commit(repo.root, "b.txt", "unreviewed change\n")
    events = _world(monkeypatch, provider, repo, body=CHECKED)

    with pytest.raises(SystemExit, match="Override AC refusé"):
        issue.merge(ISSUE, "12", flags=OVERRIDE)

    _no_effect(events)


def test_override_refuses_when_the_local_head_is_not_the_pr_head(monkeypatch, repo):
    """No adapter coordinates (YouTrack without binding): the override still fails closed."""
    _record_proof(repo, quality="blocked", verdicts=["pass", "pass"])
    events = _world(monkeypatch, YouTrackTracker, repo, body=PARTIAL, binding=None)
    issue.foundry.codehost().get_pr().sha = "f" * 40

    with pytest.raises(SystemExit, match="Override AC refusé.*coordonnées exactes"):
        issue.merge(ISSUE, "12", flags=OVERRIDE)

    _no_effect(events)
