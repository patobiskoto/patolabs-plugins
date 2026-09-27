import inspect
import subprocess

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


def _repo(tmp_path, owner, name):
    root = tmp_path / owner / name
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git", "-C", str(root), "remote", "add", "origin",
            f"https://github.com/{owner}/{name}.git",
        ],
        check=True,
    )
    return root


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


def test_setup_uses_configured_youtrack_before_a_new_repository_has_a_binding(
    monkeypatch, tmp_path,
):
    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git", "-C", str(repo), "remote", "add", "origin",
            "https://github.com/acme/demo.git",
        ],
        check=True,
    )
    provider_calls = []

    def provision(_tracker, name, short, canonical_repository):
        provider_calls.append((name, short, canonical_repository))
        return Project(key=short, id="0-42", extra={"ms_bundle": "bundle-42"})

    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setenv("FOUNDRY_TRACKER", "youtrack")
    monkeypatch.setenv("YOUTRACK_URL", "https://youtrack.example.invalid")
    monkeypatch.setenv("YOUTRACK_TOKEN", "test-token-never-sent")
    monkeypatch.setattr(YouTrackTracker, "provision_project", provision)

    setup_project.setup("Demo", "DEMO", "demo")

    assert provider_calls == [("Demo", "DEMO", None)]
    assert registry.load() == {
        "youtrack": {
            "demo": {
                "key": "DEMO", "id": "0-42", "ms_bundle": "bundle-42",
            },
        },
    }
    assert not (repo / ".foundry/tracker.json").exists()


def test_setup_retry_after_registry_interruption_does_not_duplicate_project_or_binding(
    monkeypatch, tmp_path,
):
    repo = _repo(tmp_path, "acme", "trame")
    missing = DevHubTrackerError(
        "GET", "/projects/resolve", 404, "project_not_found",
    )
    tracker = ScriptedDevHubTracker([missing, PROJECT_RAW, PROJECT_RAW, PROJECT_RAW])
    selected_providers = []
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    monkeypatch.setattr(
        foundry, "tracker",
        lambda name=None: selected_providers.append(name) or tracker,
    )
    monkeypatch.chdir(repo)
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
    assert selected_providers == ["devhub", "devhub"]
    assert registry.load() == {
        "devhub": {
            "trame": {
                "key": "TRAME", "id": "project-42",
                "canonical_repo": CANONICAL_REPOSITORY,
            },
        },
    }


def test_unsupported_provider_refuses_before_resolution_or_mutation(monkeypatch, tmp_path):
    repo = _repo(tmp_path, "acme", "demo")
    tracker = GitHubProjectsTracker()
    with pytest.raises(ProjectProvisioningUnavailableError, match="ghprojects"):
        tracker.provision_project("Demo", "DEMO")

    monkeypatch.setenv("FOUNDRY_TRACKER", "ghprojects")
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.chdir(repo)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(
        setup_project.registry, "register",
        lambda *_args, **_kwargs: pytest.fail("unsupported provider must not register"),
    )

    with pytest.raises(SystemExit, match="ne prend pas en charge.*registry bootstrap"):
        setup_project.setup("Demo", "DEMO", "demo")


@pytest.mark.parametrize("existing_mode", ["legacy", "v1-without-marker"])
def test_setup_refuses_existing_binding_before_provider_selection_or_registry_write(
    monkeypatch, tmp_path, existing_mode,
):
    repo = _repo(tmp_path, "acme", "demo")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    extra = (
        {"canonical_repo": "github.com/acme/demo"}
        if existing_mode == "v1-without-marker" else {}
    )
    registry.register("youtrack", "demo", "DEMO", "0-1", **extra)
    provider_calls = []
    registry_writes = []
    monkeypatch.setattr(
        foundry.config,
        "tracker_name",
        lambda: provider_calls.append("config") or "devhub",
    )
    monkeypatch.setattr(
        foundry,
        "tracker",
        lambda *_args, **_kwargs: provider_calls.append("factory"),
    )
    monkeypatch.setattr(
        setup_project.registry,
        "register",
        lambda *_args, **_kwargs: registry_writes.append("register"),
    )

    with pytest.raises(SystemExit, match="Setup tracker refusé"):
        setup_project.setup("Demo", "DEMO", "demo")

    assert provider_calls == []
    assert registry_writes == []
