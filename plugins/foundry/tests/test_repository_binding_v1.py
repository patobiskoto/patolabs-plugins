import json
import subprocess
from types import SimpleNamespace

import pytest

import foundry
from foundry import registry
from foundry.models import Project
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.youtrack import YouTrackTracker


def _repo(tmp_path, owner, name, remote=None):
    root = tmp_path / owner / name
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git", "-C", str(root), "remote", "add", "origin",
            remote or f"https://github.com/{owner}/{name}.git",
        ],
        check=True,
    )
    return root


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setenv("FOUNDRY_TRACKER", "youtrack")
    monkeypatch.delenv("PROJECT_REPO", raising=False)
    return tmp_path


def test_new_checkout_never_becomes_legacy_from_global_provider(isolated):
    repo = _repo(isolated, "first", "same")

    with pytest.raises(SystemExit, match="Binding tracker absent"):
        registry.repository_tracker_selection(str(repo))


def test_normal_factory_stays_closed_for_new_unbound_repository(isolated, monkeypatch):
    repo = _repo(isolated, "first", "same")
    monkeypatch.chdir(repo)

    with pytest.raises(SystemExit, match="Binding tracker absent"):
        foundry.tracker()


def test_legacy_binding_is_observable_and_explicit_v1_refuses(isolated):
    repo = _repo(isolated, "first", "same")
    registry.register("youtrack", "same", "ONE", "0-1")

    selected = registry.repository_tracker_selection(str(repo))
    assert selected["mode"] == "legacy"
    assert selected["tracker"] == "youtrack"
    with pytest.raises(SystemExit, match="V1 requis"):
        registry.repository_tracker_selection(str(repo), require_v1=True)


def test_upgrade_binds_exact_remote_and_ignores_adverse_environment(isolated, monkeypatch):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.register("youtrack", "same", "ONE", "0-1")

    binding = registry.upgrade_legacy_binding("youtrack", "same", cwd=str(first))
    monkeypatch.setenv("PROJECT_REPO", "decoy")
    monkeypatch.setenv("FOUNDRY_TRACKER", "linear")

    assert binding.activation_kind == "upgrade"
    assert binding.migration_manifest_digest is None
    assert registry.repository_tracker_selection(str(first))["tracker"] == "youtrack"
    with pytest.raises(SystemExit, match="Binding tracker absent"):
        registry.repository_tracker_selection(str(second))


def test_bootstrap_marker_has_no_fabricated_migration_manifest(isolated):
    repo = _repo(isolated, "acme", "app", "git@github.com:Acme/App.git")

    binding = registry.bootstrap_repository_binding(
        "youtrack", "App", "APP", "0-2", cwd=str(repo), ms_bundle="bundle-2",
    )
    marker = json.loads((repo / ".foundry/tracker.json").read_text())

    assert binding.activation_kind == "bootstrap"
    assert binding.migration_manifest_digest is None
    assert marker["activation"] == {"kind": "bootstrap"}
    assert "migration_manifest_digest" not in marker
    assert registry.resolve_canonical_repository(
        "youtrack", "https://github.com/acme/app.git",
    ) == binding.project


def test_bootstrap_resolves_a_linked_git_worktree_from_its_own_root(isolated):
    source = _repo(isolated, "acme", "source")
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (source / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
    worktree = isolated / "linked-worktree"
    subprocess.run(
        ["git", "-C", str(source), "worktree", "add", "-q", "-b", "fixture", str(worktree)],
        check=True,
    )

    binding = registry.bootstrap_repository_binding(
        "youtrack", "source", "SRC", "0-3", cwd=str(worktree),
    )

    assert (worktree / ".foundry/tracker.json").is_file()
    assert not (source / ".foundry/tracker.json").exists()
    assert registry.repository_tracker_selection(str(worktree))["binding"] == binding


def test_same_basename_repositories_never_share_v1_binding(isolated):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.bootstrap_repository_binding(
        "youtrack", "same", "ONE", "0-1", cwd=str(first),
    )

    with pytest.raises(SystemExit, match="Binding tracker absent"):
        registry.repository_tracker_selection(str(second))


def test_provider_readback_refuses_foreign_and_unavailable_coordinates(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")
    outcomes = iter([False, RuntimeError("provider down"), True])
    def verify(_project):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    adapter = SimpleNamespace(verify_project_identity=verify)
    monkeypatch.setattr(foundry, "tracker", lambda *_args, **_kwargs: adapter)

    with pytest.raises(ValueError, match="étrangères"):
        registry.verify_existing_project(
            "youtrack", "app", "APP", "0-1", cwd=str(repo),
        )
    with pytest.raises(ValueError, match="indisponibles"):
        registry.verify_existing_project(
            "youtrack", "app", "APP", "0-1", cwd=str(repo),
        )
    project = registry.verify_existing_project(
        "youtrack", "app", "APP", "0-1", cwd=str(repo),
    )
    assert project.extra["canonical_repo"] == "github.com/acme/app"


def test_mapping_update_publishes_binding_and_marker_together_and_refuses_stale_writer(
    isolated,
):
    repo = _repo(isolated, "acme", "app")
    initial = registry.bootstrap_repository_binding(
        "youtrack", "app", "APP", "0-1", cwd=str(repo), ms_bundle="old",
    )
    updated = registry.update_repository_binding(
        "youtrack",
        "app",
        "APP",
        "0-1",
        expected_configuration_digest=initial.configuration_digest,
        cwd=str(repo),
        ms_bundle="new",
    )

    assert updated.project.extra["ms_bundle"] == "new"
    assert registry.repository_tracker_binding(str(repo)) == updated
    with pytest.raises(ValueError, match="configuration concurrente"):
        registry.update_repository_binding(
            "youtrack",
            "app",
            "APP",
            "0-1",
            expected_configuration_digest=initial.configuration_digest,
            cwd=str(repo),
            ms_bundle="loser",
        )


def test_mapping_update_replay_completes_interrupted_marker_publication(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")
    initial = registry.bootstrap_repository_binding(
        "youtrack", "app", "APP", "0-1", cwd=str(repo), ms_bundle="old",
    )
    real_replace = registry.os.replace
    interrupted = False

    def replace(source, destination):
        nonlocal interrupted
        if str(destination).endswith(".foundry/tracker.json") and not interrupted:
            interrupted = True
            raise OSError("marker interrupted")
        return real_replace(source, destination)

    monkeypatch.setattr(registry.os, "replace", replace)
    with pytest.raises(OSError, match="marker interrupted"):
        registry.update_repository_binding(
            "youtrack", "app", "APP", "0-1",
            expected_configuration_digest=initial.configuration_digest,
            cwd=str(repo), ms_bundle="new",
        )
    assert registry.load()["youtrack"]["app"]["ms_bundle"] == "new"
    with pytest.raises(ValueError, match="modifié depuis le cutover"):
        registry.repository_tracker_binding(str(repo))

    recovered = registry.update_repository_binding(
        "youtrack", "app", "APP", "0-1",
        expected_configuration_digest=initial.configuration_digest,
        cwd=str(repo), ms_bundle="new",
    )
    assert recovered.project.extra["ms_bundle"] == "new"


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("version", 3),
        ("activation", {"kind": "unknown"}),
        ("registry_binding_digest", "sha256:invalid"),
        (
            "activation",
            {"kind": "migration", "manifest_digest": "sha256:invalid"},
        ),
    ],
)
def test_mapping_update_replay_refuses_invalid_marker_before_any_write(
    isolated, field, invalid,
):
    repo = _repo(isolated, "acme", "app")
    initial = registry.bootstrap_repository_binding(
        "youtrack", "app", "APP", "0-1", cwd=str(repo), ms_bundle="old",
    )

    # Reproduce the only valid replay window: the registry has the complete winner,
    # while the marker still describes the old binding. The malformed marker must be
    # rejected before either file is touched.
    data = registry.load()
    data["youtrack"]["app"]["ms_bundle"] = "new"
    registry._save(data)
    marker_path = repo / ".foundry/tracker.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker[field] = invalid
    if field != "configuration_digest":
        marker["configuration_digest"] = registry._json_digest({
            name: value for name, value in marker.items()
            if name != "configuration_digest"
        })
    marker_path.write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    registry_before = (isolated / "state" / "registry.json").read_bytes()
    marker_before = marker_path.read_bytes()

    with pytest.raises(ValueError, match="marqueur tracker.*invalide"):
        registry.update_repository_binding(
            "youtrack", "app", "APP", "0-1",
            expected_configuration_digest=initial.configuration_digest,
            cwd=str(repo), ms_bundle="new",
        )

    assert (isolated / "state" / "registry.json").read_bytes() == registry_before
    assert marker_path.read_bytes() == marker_before


@pytest.mark.parametrize("adapter", [YouTrackTracker, GitHubProjectsTracker])
def test_adapters_use_the_same_checkout_binding_resolution(isolated, adapter):
    repo = _repo(isolated, "acme", "app")
    extra = {} if adapter is YouTrackTracker else {"owner": "acme", "number": "7"}
    project_id = "0-1" if adapter is YouTrackTracker else "PVT_project_node"
    expected = registry.bootstrap_repository_binding(
        "youtrack" if adapter is YouTrackTracker else "ghprojects",
        "app", "APP", project_id, cwd=str(repo), **extra,
    ).project
    instance = object.__new__(adapter) if adapter is YouTrackTracker else adapter()

    assert instance.resolve_checkout_project(str(repo)) == expected


def test_factory_activates_v1_write_binding_for_marker_adapter(isolated, monkeypatch):
    repo = _repo(isolated, "acme", "app")
    expected = registry.bootstrap_repository_binding(
        "ghprojects", "app", "APP", "PVT_1", cwd=str(repo),
        owner="acme", number="7",
    ).project
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")

    adapter = foundry.tracker()

    assert adapter.name == "ghprojects"
    assert adapter.requires_mutation_binding is True
    assert adapter.resolve_checkout_project() == expected


def test_ghprojects_identity_probe_is_read_only_and_exact():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({
                "data": {
                    "organization": {"projectV2": {
                        "id": "PVT_1", "number": 7,
                        "repositories": {
                            "nodes": [{"nameWithOwner": "Acme/App"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }},
                    "user": None,
                }
            }),
            stderr="",
        )

    tracker = GitHubProjectsTracker(runner=runner)
    project = Project(
        key="APP", id="PVT_1",
        extra={"canonical_repo": "github.com/acme/app", "owner": "acme", "number": "7"},
    )

    assert tracker.verify_project_identity(project) is True
    assert calls[0][0][:3] == ["gh", "api", "graphql"]
    assert "mutation" not in calls[0][0][4].lower()
    assert calls[0][1]["check"] is False


def test_ghprojects_identity_probe_paginates_repository_membership():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        first_page = len(calls) == 1
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({
                "data": {
                    "organization": {"projectV2": {
                        "id": "PVT_1", "number": 7,
                        "repositories": {
                            "nodes": [{"nameWithOwner": "Acme/Other"}]
                            if first_page else [{"nameWithOwner": "Acme/App"}],
                            "pageInfo": {
                                "hasNextPage": first_page,
                                "endCursor": "cursor-1" if first_page else None,
                            },
                        },
                    }},
                    "user": None,
                }
            }),
            stderr="",
        )

    tracker = GitHubProjectsTracker(runner=runner)
    project = Project(
        key="APP", id="PVT_1",
        extra={"canonical_repo": "github.com/acme/app", "owner": "acme", "number": "7"},
    )

    assert tracker.verify_project_identity(project) is True
    assert len(calls) == 2
    assert calls[1][0][-2:] == ["-f", "cursor=cursor-1"]


def test_ghprojects_identity_probe_rejects_graphql_partial_errors():
    def runner(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({
                "errors": [{"type": "FORBIDDEN", "message": "read:project required"}],
                "data": {
                    "organization": {"projectV2": {
                        "id": "PVT_1", "number": 7,
                        "repositories": {
                            "nodes": [{"nameWithOwner": "Acme/App"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }},
                    "user": None,
                },
            }),
            stderr="",
        )

    tracker = GitHubProjectsTracker(runner=runner)
    project = Project(
        key="APP", id="PVT_1",
        extra={"canonical_repo": "github.com/acme/app", "owner": "acme", "number": "7"},
    )

    assert tracker.verify_project_identity(project) is False


def test_ghprojects_transport_exception_is_unavailable_before_publication(
    isolated, monkeypatch,
):
    repo = _repo(isolated, "acme", "app")

    def runner(*_args, **_kwargs):
        raise OSError("gh transport unavailable")

    adapter = GitHubProjectsTracker(runner=runner)
    monkeypatch.setattr(foundry, "tracker", lambda *_args, **_kwargs: adapter)

    with pytest.raises(ValueError, match="indisponibles"):
        registry.verify_existing_project(
            "ghprojects", "app", "APP", "PVT_1", cwd=str(repo),
            owner="acme", number="7",
        )
    assert registry.load().get("ghprojects", {}) == {}
    assert not (repo / ".foundry/tracker.json").exists()
