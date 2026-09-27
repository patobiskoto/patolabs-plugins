import json
import subprocess
from types import SimpleNamespace

import pytest

import foundry
from foundry import doctor, registry
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


@pytest.mark.parametrize("state", ["interrupted", "ambiguous", "tombstone"])
def test_factory_and_doctor_refuse_existing_state_before_global_devhub(
    isolated, monkeypatch, state,
):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    registry.register(
        "youtrack", "same", "ONE", "0-1",
        canonical_repo="github.com/acme/same",
    )
    if state == "ambiguous":
        registry.register(
            "ghprojects", "same", "TWO", "PVT_2",
            canonical_repo="github.com/acme/same", owner="acme", number="2",
        )
    elif state == "tombstone":
        data = registry.load()
        data["youtrack"]["same"]["archive"] = True
        registry._save(data)
    monkeypatch.setattr(
        foundry.config, "tracker_name",
        lambda: pytest.fail("existing local state must be checked before global config"),
    )
    monkeypatch.setattr(
        foundry.config, "require_public",
        lambda _key: pytest.fail("no provider construction or preflight allowed"),
    )
    for call in (foundry.tracker, doctor._provider_transport_preflight):
        with pytest.raises(SystemExit, match="Binding tracker"):
            call()


def test_legacy_selection_beats_global_devhub_in_factory_and_doctor(isolated, monkeypatch):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    registry.register("ghprojects", "same", "ONE", "PVT_1")
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    assert foundry.tracker().name == "ghprojects"
    assert doctor._provider_transport_preflight() == ("ghprojects", None)


def test_devhub_pilot_requires_unbound_or_its_own_unambiguous_binding(isolated, monkeypatch):
    repo = _repo(isolated, "acme", "same")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    assert foundry.effective_tracker_name() == "devhub"
    registry.register(
        "devhub", "same", "PILOT", "42", canonical_repo="github.com/acme/same",
    )
    assert foundry.effective_tracker_name() == "devhub"
    assert registry.repository_tracker_selection()["mode"] == "pilot"
    with pytest.raises(SystemExit, match="V1 requis"):
        registry.repository_tracker_selection(require_v1=True)
    registry.register("youtrack", "same", "OTHER", "0-1")
    with pytest.raises(SystemExit, match="ambigu"):
        foundry.effective_tracker_name()


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


def test_same_basename_unbound_repository_never_inherits_foreign_v1_binding(isolated):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.bootstrap_repository_binding(
        "youtrack", "same", "ONE", "0-1", cwd=str(first),
    )

    with pytest.raises(SystemExit, match="Binding tracker absent"):
        registry.repository_tracker_selection(str(second))


def test_same_basename_repositories_have_independent_canonical_bindings(
    isolated, monkeypatch,
):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    first_binding = registry.bootstrap_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1", cwd=str(first),
        owner="first", number="1",
    )
    second_binding = registry.bootstrap_repository_binding(
        "ghprojects", "same", "TWO", "PVT_2", cwd=str(second),
        owner="second", number="2",
    )
    entries = registry.load()["ghprojects"]

    assert entries["same"]["canonical_repo"] == "github.com/first/same"
    disambiguated = [name for name in entries if name != "same"]
    assert disambiguated == [
        registry._disambiguated_registry_key("same", "github.com/second/same")
    ]

    monkeypatch.setenv("PROJECT_REPO", "previous-checkout")
    monkeypatch.setenv("FOUNDRY_TRACKER", "devhub")
    for root, expected in (
        (first, first_binding.project),
        (second, second_binding.project),
    ):
        monkeypatch.chdir(root)
        adapter = foundry.tracker()
        assert adapter.name == "ghprojects"
        assert adapter.resolve_checkout_project() == expected
        assert doctor._provider_transport_preflight() == ("ghprojects", None)

    first_updated = registry.update_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1",
        expected_configuration_digest=first_binding.configuration_digest,
        cwd=str(first), owner="first", number="3",
    )
    second_updated = registry.update_repository_binding(
        "ghprojects", "same", "TWO", "PVT_2",
        expected_configuration_digest=second_binding.configuration_digest,
        cwd=str(second), owner="second", number="4",
    )

    assert first_updated.project.extra["number"] == "3"
    assert second_updated.project.extra["number"] == "4"
    assert registry.repository_tracker_binding(str(first)) == first_updated
    assert registry.repository_tracker_binding(str(second)) == second_updated


def test_disambiguated_binding_update_replay_completes_marker_publication(
    isolated, monkeypatch,
):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.bootstrap_repository_binding(
        "ghprojects", "same", "ONE", "PVT_1", cwd=str(first),
        owner="first", number="1",
    )
    initial = registry.bootstrap_repository_binding(
        "ghprojects", "same", "TWO", "PVT_2", cwd=str(second),
        owner="second", number="2",
    )
    real_replace = registry.os.replace
    interrupted = False

    def replace(source, destination):
        nonlocal interrupted
        if str(destination).endswith("second/same/.foundry/tracker.json") and not interrupted:
            interrupted = True
            raise OSError("marker interrupted")
        return real_replace(source, destination)

    monkeypatch.setattr(registry.os, "replace", replace)
    with pytest.raises(OSError, match="marker interrupted"):
        registry.update_repository_binding(
            "ghprojects", "same", "TWO", "PVT_2",
            expected_configuration_digest=initial.configuration_digest,
            cwd=str(second), owner="second", number="9",
        )

    recovered = registry.update_repository_binding(
        "ghprojects", "same", "TWO", "PVT_2",
        expected_configuration_digest=initial.configuration_digest,
        cwd=str(second), owner="second", number="9",
    )

    assert recovered.project.extra["number"] == "9"
    assert registry.repository_tracker_binding(str(first)).project.key == "ONE"


def test_cutover_ignores_foreign_canonical_binding_with_same_basename(isolated):
    first = _repo(isolated, "first", "same")
    second = _repo(isolated, "second", "same")
    registry.register(
        "youtrack", "same", "ONE", "0-1",
        canonical_repo=registry.checkout_repository_identity(str(first)),
    )
    registry.register(
        "ghprojects", "second-same", "TWO", "PVT_2",
        canonical_repo=registry.checkout_repository_identity(str(second)),
        owner="second", number="2",
    )

    binding = registry.cutover_repository_tracker(
        "ghprojects", "TWO", "PVT_2",
        migration_manifest_digest="sha256:" + "a" * 64,
        cwd=str(second),
    )

    assert binding.project.key == "TWO"
    assert registry.load()["youtrack"]["same"].get("archive") is not True
    assert registry.repository_tracker_binding(str(second)) == binding


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
