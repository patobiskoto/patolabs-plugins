import inspect

import pytest

import foundry
from foundry import registry, setup_project
from foundry.models import Project
from foundry.trackers.devhub import DevHubTracker, DevHubTrackerError
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.base import ProjectProvisioningUnavailableError
from foundry.trackers.youtrack import YouTrackTracker


TOKEN = "tracker-token-at-least-twenty-four"
SECRET = "proof-secret-at-least-thirty-two-characters"
CANONICAL_REPOSITORY = "github.com/acme/trame"
PROJECT_RAW = {
    "key": "TRAME", "id": "project-42",
    "canonical_repo": CANONICAL_REPOSITORY,
    "extra": {
        "opaque_provider_metadata": "must-not-enter-the-registry",
    },
}


class ScriptedDevHubTracker(DevHubTracker):
    def __init__(self, responses):
        super().__init__(
            url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET,
            idempotency_factory=lambda: "unused-random-idempotency",
        )
        self.responses = list(responses)
        self.calls = []

    def _req(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {path}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_setup_core_has_no_concrete_youtrack_decision():
    source = inspect.getsource(setup_project)

    assert "YouTrackTracker" not in source
    assert "trackers.youtrack" not in source


def test_youtrack_capability_preserves_provider_specific_provisioning(monkeypatch):
    tracker = object.__new__(YouTrackTracker)
    expected = Project(key="DEMO", id="0-1", extra={"ms_bundle": "bundle-1"})
    calls = []

    monkeypatch.setattr(
        "foundry.trackers.youtrack_provisioning.provision_youtrack_project",
        lambda candidate, name, key: calls.append((candidate, name, key)) or expected,
    )

    assert tracker.project_provisioning_supported is True
    assert tracker.project_provisioning_requires_repository is False
    assert tracker.provision_project("Demo", "DEMO") == expected
    assert calls == [(tracker, "Demo", "DEMO")]


def test_youtrack_capability_recovers_existing_project_without_mutation():
    class ExistingYouTrack(YouTrackTracker):
        def __init__(self):
            self.calls = []

        def _req(self, method, path, body=None, fields=None, top=None):
            self.calls.append((method, path, body, fields, top))
            assert method == "GET"
            if path == "/admin/projects":
                return [{"id": "0-1", "shortName": "DEMO"}]
            if path == "/admin/customFieldSettings/customFields":
                return [
                    {"id": "state", "name": "State", "instances": [{
                        "bundle": {"id": "states", "$type": "StateBundle"},
                    }]},
                    {"id": "priority", "name": "Priority", "instances": [{
                        "bundle": {"id": "priorities", "$type": "EnumBundle"},
                    }]},
                    {"id": "type", "name": "Type", "instances": [{
                        "bundle": {"id": "types", "$type": "EnumBundle"},
                    }]},
                    {"id": "estimate", "name": "Estimate", "instances": []},
                    {"id": "labels", "name": "Labels", "instances": []},
                    {"id": "pr", "name": "GitHub PR", "instances": []},
                ]
            if path.endswith("/bundles/state/states/values"):
                return [{"name": name} for name in (
                    "backlog", "ready", "in-progress", "review", "blocked",
                    "done", "dropped",
                )]
            if path.endswith("/bundles/enum/priorities/values"):
                return [{"name": name} for name in ("P0", "P1", "P2", "P3")]
            if path.endswith("/bundles/enum/types/values"):
                return [{"name": name} for name in (
                    "Epic", "Feature", "Bug", "Task",
                )]
            if path == "/admin/projects/0-1/customFields":
                if fields == "field(name)":
                    return [{"field": {"name": name}} for name in (
                        "State", "Priority", "Type", "Estimate", "Labels",
                        "GitHub PR", "Milestone",
                    )]
                return [{
                    "field": {"name": "Milestone"},
                    "bundle": {"id": "demo-milestones"},
                }]
            raise AssertionError(path)

    tracker = ExistingYouTrack()

    project = tracker.provision_project("Demo", "DEMO")

    assert project == Project(
        key="DEMO", id="0-1", extra={"ms_bundle": "demo-milestones"},
    )
    assert all(call[0] == "GET" for call in tracker.calls)


def test_devhub_capability_creates_and_confirms_canonical_binding():
    missing = DevHubTrackerError(
        "GET", "/projects/resolve", 404, "project_not_found",
    )
    tracker = ScriptedDevHubTracker([missing, PROJECT_RAW, PROJECT_RAW])

    project = tracker.provision_project(
        "Trame", "TRAME", "git@github.com:Acme/Trame.git",
    )

    assert project == Project(
        key="TRAME", id="project-42",
        extra={"canonical_repo": CANONICAL_REPOSITORY},
    )
    assert [call[:2] for call in tracker.calls] == [
        ("GET", "/projects/resolve?repo=github.com%2Facme%2Ftrame"),
        ("POST", "/projects"),
        ("GET", "/projects/resolve?repo=github.com%2Facme%2Ftrame"),
    ]
    create_call = tracker.calls[1]
    assert create_call[2] == {
        "name": "Trame", "slug": "trame", "ticker": "TRAME",
        "repository": CANONICAL_REPOSITORY,
    }
    assert create_call[3]["idempotent"] is True
    assert create_call[3]["idempotency_key"].startswith("foundry-project-")


def test_devhub_capability_recovers_existing_project_without_create():
    tracker = ScriptedDevHubTracker([PROJECT_RAW])

    project = tracker.provision_project("Trame", "TRAME", CANONICAL_REPOSITORY)

    assert project.id == "project-42"
    assert [call[0] for call in tracker.calls] == ["GET"]


def test_devhub_capability_refuses_an_unconfirmed_repository_binding():
    raw = {"key": "TRAME", "id": "project-42", "extra": {}}
    tracker = ScriptedDevHubTracker([raw])

    with pytest.raises(DevHubTrackerError, match="binding_mismatch"):
        tracker.provision_project("Trame", "TRAME", CANONICAL_REPOSITORY)

    assert [call[0] for call in tracker.calls] == ["GET"]


def test_setup_retry_after_registry_interruption_does_not_duplicate_project_or_binding(
    monkeypatch, tmp_path,
):
    missing = DevHubTrackerError(
        "GET", "/projects/resolve", 404, "project_not_found",
    )
    tracker = ScriptedDevHubTracker([missing, PROJECT_RAW, PROJECT_RAW, PROJECT_RAW])
    monkeypatch.setattr(foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(
        setup_project.registry, "checkout_repository_identity",
        lambda: CANONICAL_REPOSITORY,
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    real_replace = registry.os.replace
    attempts = 0

    def interrupted_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated registry interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(registry.os, "replace", interrupted_replace)

    with pytest.raises(OSError, match="simulated registry interruption"):
        setup_project.setup("Trame", "TRAME", "trame")
    assert registry.load() == {}
    assert not list(tmp_path.glob(".registry-*.json.tmp"))
    setup_project.setup("Trame", "TRAME", "trame")

    assert [call[0] for call in tracker.calls].count("POST") == 1
    assert registry.load() == {
        "devhub": {
            "trame": {
                "key": "TRAME", "id": "project-42",
                "canonical_repo": CANONICAL_REPOSITORY,
            },
        },
    }


def test_unsupported_provider_refuses_before_resolution_or_mutation(monkeypatch):
    tracker = GitHubProjectsTracker()
    with pytest.raises(ProjectProvisioningUnavailableError, match="ghprojects"):
        tracker.provision_project("Demo", "DEMO")

    monkeypatch.setattr(foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(
        setup_project.registry, "checkout_repository_identity",
        lambda: pytest.fail("unsupported provider must fail during capability preflight"),
    )
    monkeypatch.setattr(
        setup_project.registry, "register",
        lambda *_args, **_kwargs: pytest.fail("unsupported provider must not register"),
    )

    with pytest.raises(SystemExit, match="ne prend pas en charge.*registry register"):
        setup_project.setup("Demo", "DEMO", "demo")
