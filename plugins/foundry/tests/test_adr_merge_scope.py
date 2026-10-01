import pytest

from foundry import adr
from foundry.models import Adr, Issue, Project


class _Tracker:
    requires_mutation_binding = False

    def __init__(self, body):
        self.issue = Issue(id="PAT-78", title="Synthetic delivery", body=body)
        self.adr = Adr(id="PAT-ADR-0001", title="Synthetic ADR", status="proposed")
        self.status_writes = []

    def resolve_project(self, _repo):
        return Project(key="PAT", id="project")

    def get_issue(self, issue_id):
        assert issue_id == self.issue.id
        return self.issue

    def list_adrs(self, _project):
        return [self.adr]

    def set_adr_status(self, candidate, status, **_kwargs):
        self.status_writes.append((candidate.id, status))


def test_automated_accept_refuses_project_index_membership_without_issue_frame(monkeypatch):
    """PAT-78-style index membership cannot trigger a provider status mutation."""
    tracker = _Tracker("## Critères d'acceptation\n- [x] merged")
    monkeypatch.setattr(adr.foundry, "tracker", lambda: tracker)

    with pytest.raises(SystemExit, match="Acceptation ADR automatique refusée"):
        adr.accept("PAT-ADR-0001", framed_by="PAT-78")

    assert tracker.status_writes == []


def test_automated_accept_promotes_the_exact_proposed_adr_framed_by_issue(monkeypatch):
    tracker = _Tracker(
        "## Scope\n\n---\n**Cadre (ADR) :** PAT-ADR-0001\n"
    )
    monkeypatch.setattr(adr.foundry, "tracker", lambda: tracker)

    adr.accept("PAT-ADR-0001", framed_by="PAT-78")

    assert tracker.status_writes == [("PAT-ADR-0001", "accepted")]


def test_explicit_human_accept_does_not_require_an_issue_frame(monkeypatch):
    tracker = _Tracker("unrelated issue body")
    monkeypatch.setattr(adr.foundry, "tracker", lambda: tracker)

    adr.accept("PAT-ADR-0001")

    assert tracker.status_writes == [("PAT-ADR-0001", "accepted")]


@pytest.mark.parametrize(
    "body",
    [
        "**Cadre (ADR) :** PAT-ADR-0001\n**Cadre (ADR) :** PAT-ADR-0001",
        "**Cadre (ADR) :** PAT-ADR-0001\n**Cadre (ADR):** PAT-ADR-0002",
        "**Cadre (ADR) :** PAT-ADR-0001, PAT-ADR-0001",
        "**Cadre (ADR) :** PAT-ADR-0001, ???",
    ],
)
def test_automated_accept_refuses_ambiguous_frame_before_effect(monkeypatch, body):
    tracker = _Tracker(body)
    monkeypatch.setattr(adr.foundry, "tracker", lambda: tracker)

    with pytest.raises(SystemExit, match="Acceptation ADR automatique refusée"):
        adr.accept("PAT-ADR-0001", framed_by="PAT-78")

    assert tracker.status_writes == []
