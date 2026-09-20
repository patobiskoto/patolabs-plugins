from dataclasses import replace

import pytest

from foundry import issue, write
from foundry.models import (
    EpicClosureChild,
    EpicClosureOutcome,
    EpicClosureReceipt,
    Issue,
    Link,
    Project,
)
from foundry.trackers.base import (
    EpicClosureUnavailableError,
    Tracker,
    TrackerConflictError,
)
from foundry.trackers.devhub import DevHubTracker, DevHubTrackerError
from foundry.trackers.youtrack import YouTrackTracker


class AtomicEpicTracker:
    """Fake provider whose close method is the atomic graph boundary."""

    name = "atomic-fixture"
    epic_closure_supported = True
    requires_mutation_binding = False

    def __init__(self, *, ac_done=2, ac_total=2):
        self.project = Project(key="DEMO", id="project-1")
        self.issues = {
            "DEMO-1": Issue(
                id="DEMO-1",
                title="Non-code release",
                state="in-progress",
                type="Epic",
                ac_done=ac_done,
                ac_total=ac_total,
                version=7,
                links=[
                    Link(type="parent-of", direction="outward", target="DEMO-3"),
                    Link(type="parent-of", direction="outward", target="DEMO-2"),
                ],
            ),
            "DEMO-2": Issue(
                id="DEMO-2", title="Decision", state="done", type="Feature", version=4,
            ),
            "DEMO-3": Issue(
                id="DEMO-3", title="Research", state="dropped", type="Feature", version=9,
            ),
        }
        self.before_commit = None
        self.interrupt_after_commit = False
        self.close_calls = 0
        self.recovery_reads = 0
        self.get_calls = []
        self.binding_calls = []
        self.stored = None

    def resolve_project(self, repo):
        assert isinstance(repo, str)
        return self.project

    def validate_issue_binding(self, project, *issue_ids):
        self.binding_calls.append((project, issue_ids))
        if project != self.project or any(
            not issue_id.startswith(f"{project.key}-") for issue_id in issue_ids
        ):
            raise ValueError("foreign project")

    def get_issue(self, issue_id):
        self.get_calls.append(issue_id)
        return self.issues[issue_id]

    def _current_receipt_coordinates(self):
        parent = self.issues["DEMO-1"]
        child_ids = sorted(
            relation.target for relation in parent.links
            if relation.type == "parent-of"
        )
        return parent, tuple(
            EpicClosureChild(
                id=child_id,
                version=self.issues[child_id].version,
                state=self.issues[child_id].state,
            )
            for child_id in child_ids
        )

    def close_epic(self, project, receipt):
        self.close_calls += 1
        if self.before_commit is not None:
            self.before_commit()
        parent, children = self._current_receipt_coordinates()
        if (
            project != self.project
            or receipt.project_key != project.key
            or receipt.project_id != project.id
            or receipt.parent_id != parent.id
            or receipt.parent_version != parent.version
            or receipt.parent_type != parent.type
            or receipt.parent_ac_done != parent.ac_done
            or receipt.parent_ac_total != parent.ac_total
            or receipt.children != children
            or any(child.state not in write.EPIC_CHILD_TERMINAL_STATES for child in children)
        ):
            raise TrackerConflictError("locked graph differs")
        parent.state = "done"
        parent.version += 1
        self.stored = EpicClosureOutcome(
            receipt=receipt,
            closed_parent_version=parent.version,
            audit_id="audit:epic:1",
        )
        if self.interrupt_after_commit:
            self.interrupt_after_commit = False
            raise RuntimeError("connection lost after commit")
        return self.stored

    def get_epic_closure(self, project, parent_id):
        self.recovery_reads += 1
        if project != self.project or parent_id != "DEMO-1" or self.stored is None:
            return None
        return replace(self.stored, replayed=True)


@pytest.mark.parametrize(("ac_done", "ac_total"), [(0, 0), (2, 2)])
def test_close_epic_binds_exact_project_parent_children_version_time_and_nonce(
    monkeypatch, ac_done, ac_total,
):
    tracker = AtomicEpicTracker(ac_done=ac_done, ac_total=ac_total)
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")

    outcome = write.close_epic(
        tracker,
        "DEMO-1",
        issued_at=1_800_000_000_000,
        nonce="nonce_1234567890abcdef",
    )

    assert outcome.audit_id == "audit:epic:1"
    assert outcome.closed_parent_version == 8
    assert outcome.receipt == EpicClosureReceipt(
        project_key="DEMO",
        project_id="project-1",
        parent_id="DEMO-1",
        parent_version=7,
        parent_type="Epic",
        parent_ac_done=ac_done,
        parent_ac_total=ac_total,
        children=(
            EpicClosureChild(id="DEMO-2", version=4, state="done"),
            EpicClosureChild(id="DEMO-3", version=9, state="dropped"),
        ),
        issued_at=1_800_000_000_000,
        nonce="nonce_1234567890abcdef",
    )
    assert tracker.binding_calls == [
        (tracker.project, ("DEMO-1",)),
        (tracker.project, ("DEMO-1", "DEMO-2", "DEMO-3")),
    ]


def test_close_epic_refuses_incomplete_parent_acceptance_before_provider_mutation(
    monkeypatch,
):
    tracker = AtomicEpicTracker(ac_done=1, ac_total=2)
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")

    with pytest.raises(SystemExit, match="AC propres.*incomplètes"):
        write.close_epic(tracker, "DEMO-1")

    assert tracker.close_calls == 0


def test_close_epic_refuses_open_child_before_provider_mutation(monkeypatch):
    tracker = AtomicEpicTracker()
    tracker.issues["DEMO-2"].state = "review"
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")

    with pytest.raises(SystemExit, match="DEMO-2.*pas terminal"):
        write.close_epic(tracker, "DEMO-1")

    assert tracker.close_calls == 0


def test_close_epic_refuses_cross_project_child_before_fetch_or_mutation(monkeypatch):
    tracker = AtomicEpicTracker()
    tracker.issues["DEMO-1"].links.append(
        Link(type="parent-of", direction="outward", target="OTHER-9")
    )
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")

    with pytest.raises(ValueError, match="foreign project"):
        write.close_epic(tracker, "DEMO-1")

    assert "OTHER-9" not in tracker.get_calls
    assert tracker.close_calls == 0


@pytest.mark.parametrize("race", ["added", "reopened", "version"])
def test_close_epic_provider_refuses_concurrent_graph_change(monkeypatch, race):
    tracker = AtomicEpicTracker()
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")

    def mutate_locked_graph():
        if race == "added":
            tracker.issues["DEMO-4"] = Issue(
                id="DEMO-4", title="Late child", state="done", version=1,
            )
            tracker.issues["DEMO-1"].links.append(
                Link(type="parent-of", direction="outward", target="DEMO-4")
            )
        elif race == "reopened":
            tracker.issues["DEMO-2"].state = "in-progress"
            tracker.issues["DEMO-2"].version += 1
        else:
            tracker.issues["DEMO-2"].version += 1

    tracker.before_commit = mutate_locked_graph

    with pytest.raises(TrackerConflictError, match="locked graph differs"):
        write.close_epic(tracker, "DEMO-1")

    assert tracker.issues["DEMO-1"].state == "in-progress"


def test_close_epic_retries_by_recovering_provider_audit_after_interruption(
    monkeypatch, capsys,
):
    tracker = AtomicEpicTracker()
    tracker.interrupt_after_commit = True
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)

    with pytest.raises(SystemExit, match=r"Relance exactement `issue close-epic DEMO-1`"):
        issue.close_epic("DEMO-1")
    issue.close_epic("DEMO-1")

    assert tracker.close_calls == 1
    assert tracker.recovery_reads == 1
    assert "reçu existant repris" in capsys.readouterr().out


def test_close_epic_refuses_done_parent_without_its_provider_audit(monkeypatch):
    tracker = AtomicEpicTracker()
    tracker.issues["DEMO-1"].state = "done"
    tracker.issues["DEMO-1"].version = 8
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")

    with pytest.raises(SystemExit, match="sans audit.*récupérable"):
        write.close_epic(tracker, "DEMO-1")

    assert tracker.close_calls == 0


def test_close_epic_rejects_provider_outcome_not_bound_to_exact_receipt(monkeypatch):
    tracker = AtomicEpicTracker()
    original_close = tracker.close_epic
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")

    def forged_outcome(project, receipt):
        outcome = original_close(project, receipt)
        forged = replace(receipt, parent_version=receipt.parent_version - 1)
        return replace(outcome, receipt=forged)

    tracker.close_epic = forged_outcome

    with pytest.raises(SystemExit, match="différent de la demande"):
        write.close_epic(tracker, "DEMO-1")


def test_devhub_declares_the_atomic_epic_closure_capability():
    tracker = DevHubTracker(
        url="http://127.0.0.1:3000",
        token="tracker-token-at-least-twenty-four",
        proof_secret="proof-secret-at-least-thirty-two-characters",
    )

    assert tracker.epic_closure_supported is True


def test_devhub_old_server_contract_fails_closed_without_fallback(monkeypatch):
    tracker = DevHubTracker(
        url="http://127.0.0.1:3000",
        token="tracker-token-at-least-twenty-four",
        proof_secret="proof-secret-at-least-thirty-two-characters",
    )
    calls = []

    def unsupported(method, path, body=None, **kwargs):
        calls.append((method, path, body, kwargs))
        raise DevHubTrackerError(method, path, 404, "contract_mismatch")

    monkeypatch.setattr(tracker, "_req", unsupported)

    with pytest.raises(DevHubTrackerError, match="contract_mismatch"):
        tracker.get_epic_closure(Project(key="DEMO", id="1"), "DEMO-1")

    assert calls == [(
        "GET", "/projects/DEMO/epics/DEMO-1/closure", None,
        {"response_contract": "devhub-epic-closure.v1"},
    )]


def test_youtrack_refuses_epic_closure_before_any_request(monkeypatch):
    tracker = YouTrackTracker(
        url="http://127.0.0.1:8080",
        token="tracker-token-at-least-twenty-four",
    )
    monkeypatch.setattr(
        tracker,
        "_req",
        lambda *_args, **_kwargs: pytest.fail("unsupported capability must not request"),
    )

    assert tracker.epic_closure_supported is False
    with pytest.raises(EpicClosureUnavailableError):
        write.close_epic(tracker, "DEMO-1")


def test_default_tracker_port_declares_atomic_epic_closure_unavailable():
    receipt = EpicClosureReceipt(
        project_key="DEMO",
        project_id="project-1",
        parent_id="DEMO-1",
        parent_version=1,
        parent_type="Epic",
        parent_ac_done=0,
        parent_ac_total=0,
        children=(EpicClosureChild(id="DEMO-2", version=1, state="done"),),
        issued_at=1,
        nonce="nonce_1234567890abcdef",
    )
    with pytest.raises(EpicClosureUnavailableError):
        Tracker.close_epic(
            type("Unavailable", (), {"name": "fixture"})(),
            Project(key="DEMO", id="project-1"),
            receipt,
        )


def test_code_issue_done_gate_still_requires_the_existing_merge_context():
    tracker = type(
        "ProofBound",
        (),
        {
            "bounded_transition_proofs": True,
            "set_state": lambda *_args, **_kwargs: pytest.fail("must not mutate"),
        },
    )()

    with pytest.raises(SystemExit, match="flux mécanique merge"):
        write.transition(tracker, "DEMO-9", "done")


def test_close_epic_is_a_dedicated_cli_command_without_flags():
    assert issue._KNOWN_FLAGS["close-epic"] == set()
