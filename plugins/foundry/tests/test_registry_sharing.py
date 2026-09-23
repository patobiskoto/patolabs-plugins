"""The registry must be ONE file shared by every execution context.

Codex injects PLUGIN_DATA/CLAUDE_PLUGIN_DATA into hook commands but NOT into
the bash commands run by skills. If data_dir() prefers those variables, skills
write ~/.config/foundry/registry.json while hooks read an empty plugin-private
dir — and both hooks silently no-op (they fail open). These tests pin the fix:
plugin-private env vars are ignored; only an explicit FOUNDRY_DATA overrides
the stable ~/.config/foundry home.
"""
import concurrent.futures
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from foundry import registry

_HOOKS = os.path.join(os.path.dirname(__file__), "..", "hooks")
_LINEAR_PROJECT_ID = "00000000-0000-4000-8000-000000000000"


def _clear_data_env(monkeypatch):
    for key in ("FOUNDRY_DATA", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA", "PROJECT_REPO"):
        monkeypatch.delenv(key, raising=False)


def test_data_dir_ignores_plugin_private_env(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-private"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "claude-private"))

    assert registry.data_dir() == str(tmp_path / ".config" / "foundry")


def test_explicit_foundry_data_still_wins(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "explicit"))
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-private"))

    assert registry.data_dir() == str(tmp_path / "explicit")


def test_entry_written_without_plugin_env_is_read_with_it(monkeypatch, tmp_path):
    """Skill context (no plugin env) writes; hook context (plugin env set) reads."""
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    registry.register("youtrack", "demo", "DEMO", "0-1")

    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-private"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "claude-private"))
    assert registry.load()["youtrack"]["demo"] == {"key": "DEMO", "id": "0-1"}


def _linear_binding():
    identifiers = iter(
        f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 15)
    )
    return {
        "canonical_repo": "github.com/acme/trame",
        "team_id": next(identifiers),
        "state_ids": {
            state: next(identifiers)
            for state in (
                "backlog", "ready", "in-progress", "review", "blocked", "done", "dropped",
            )
        },
        "type_label_ids": {
            kind: next(identifiers) for kind in ("Epic", "Feature", "Bug", "Task")
        },
        "milestone_ids": {"M1": next(identifiers)},
        "label_ids": {"pilot": next(identifiers)},
    }


@pytest.mark.parametrize("tracker", ["youtrack", "devhub", "ghprojects"])
def test_non_linear_register_cli_preserves_json_looking_extras_as_strings(
    monkeypatch, tmp_path, tracker,
):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))

    registry.main([
        "register", tracker, "demo", "DEMO", "project-42",
        'metadata={"nested":"value"}', 'malformed={"nested":', "team_id=team-123",
    ])

    binding = registry.load()[tracker]["demo"]
    assert binding == {
        "key": "DEMO",
        "id": "project-42",
        "metadata": '{"nested":"value"}',
        "malformed": '{"nested":',
        "team_id": "team-123",
    }
    raw = json.loads((tmp_path / "state" / "registry.json").read_text())
    assert raw[tracker]["demo"] == binding


def test_linear_registration_persists_only_a_complete_credential_free_binding(
    monkeypatch, tmp_path,
):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    extra = _linear_binding()

    registry.register(
        "linear", "trame", "TRAME", _LINEAR_PROJECT_ID, **extra,
    )

    assert registry.load()["linear"]["trame"] == {
        "key": "TRAME", "id": _LINEAR_PROJECT_ID, **extra,
    }


def test_registry_cli_accepts_a_complete_linear_binding(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    extra = _linear_binding()

    registry.main([
        "register", "linear", "trame", "TRAME", _LINEAR_PROJECT_ID,
        f"canonical_repo={extra['canonical_repo']}",
        f"team_id={extra['team_id']}",
        f"state_ids={json.dumps(extra['state_ids'])}",
        f"type_label_ids={json.dumps(extra['type_label_ids'])}",
        f"milestone_ids={json.dumps(extra['milestone_ids'])}",
        f"label_ids={json.dumps(extra['label_ids'])}",
    ])

    assert registry.load()["linear"]["trame"] == {
        "key": "TRAME", "id": _LINEAR_PROJECT_ID, **extra,
    }


@pytest.mark.parametrize("mutate", [
    lambda extra: extra.pop("state_ids"),
    lambda extra: extra["state_ids"].pop("ready"),
    lambda extra: extra["state_ids"].update({"unknown": "00000000-0000-4000-8000-000000000099"}),
    lambda extra: extra["type_label_ids"].pop("Task"),
    lambda extra: extra["type_label_ids"].update({"unknown": "00000000-0000-4000-8000-000000000099"}),
    lambda extra: extra["state_ids"].update({"ready": extra["state_ids"]["backlog"]}),
    lambda extra: extra["type_label_ids"].update({"Task": extra["type_label_ids"]["Epic"]}),
    lambda extra: extra["type_label_ids"].update({"Task": extra["state_ids"]["backlog"]}),
    lambda extra: extra.update({"team_id": "not-a-uuid"}),
    lambda extra: extra["state_ids"].update({"done": "not-a-uuid"}),
    lambda extra: extra["type_label_ids"].update({"Bug": "not-a-uuid"}),
    lambda extra: extra.update({"canonical_repo": "https://github.com/acme/trame.git"}),
    lambda extra: extra.update({"token": "SENTINEL_DO_NOT_PERSIST"}),
    lambda extra: extra.update({"endpoint": "https://private.invalid"}),
])
def test_linear_registration_refuses_invalid_bindings_without_changing_registry(
    monkeypatch, tmp_path, mutate,
):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    registry.register("youtrack", "existing", "EXISTING", "0-1")
    before = (state / "registry.json").read_bytes()
    extra = _linear_binding()
    mutate(extra)

    with pytest.raises(ValueError, match="binding Linear invalide"):
        registry.register(
            "linear", "trame", "TRAME", _LINEAR_PROJECT_ID, **extra,
        )

    assert (state / "registry.json").read_bytes() == before
    assert registry.load() == {"youtrack": {"existing": {"key": "EXISTING", "id": "0-1"}}}


@pytest.mark.parametrize(
    "collision",
    ["repository-project", "team-project", "state-project", "label-team"],
)
def test_linear_registration_rejects_global_identifier_collisions_without_writing(
    monkeypatch, tmp_path, collision,
):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    registry.register("youtrack", "existing", "EXISTING", "0-1")
    registry_path = state / "registry.json"
    before = registry_path.read_bytes()
    repository = "trame"
    project_id = _LINEAR_PROJECT_ID
    extra = _linear_binding()

    if collision == "repository-project":
        repository = project_id
    elif collision == "team-project":
        extra["team_id"] = project_id
    elif collision == "state-project":
        project_id = extra["state_ids"]["backlog"]
    else:
        extra["label_ids"]["pilot"] = extra["team_id"]

    with pytest.raises(ValueError, match="binding Linear invalide"):
        registry.register("linear", repository, "TRAME", project_id, **extra)

    assert registry_path.read_bytes() == before
    assert registry.load() == {"youtrack": {"existing": {"key": "EXISTING", "id": "0-1"}}}


@pytest.mark.parametrize(("map_name", "name", "identifier"), [
    ("label_ids", "https://private.invalid/label", "00000000-0000-4000-8000-000000000099"),
    ("milestone_ids", "api_token", "00000000-0000-4000-8000-000000000099"),
    ("label_ids", "pilot", "https://private.invalid/identifier"),
    ("milestone_ids", "M1", "SENTINEL_SECRET_DO_NOT_PERSIST"),
])
def test_linear_registration_rejects_url_or_secret_map_content_without_writing(
    monkeypatch, tmp_path, map_name, name, identifier,
):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    registry.register("youtrack", "existing", "EXISTING", "0-1")
    registry_path = state / "registry.json"
    before = registry_path.read_bytes()
    extra = _linear_binding()
    extra[map_name] = {name: identifier}

    with pytest.raises(ValueError, match="binding Linear invalide"):
        registry.register("linear", "trame", "TRAME", _LINEAR_PROJECT_ID, **extra)

    assert registry_path.read_bytes() == before
    assert registry.load() == {"youtrack": {"existing": {"key": "EXISTING", "id": "0-1"}}}


@pytest.mark.parametrize("structured_value", ['{"open":}', "[]"])
def test_register_cli_rejects_malformed_json_object_without_writing(
    monkeypatch, tmp_path, structured_value,
):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    registry.register("youtrack", "existing", "EXISTING", "0-1")
    registry_path = state / "registry.json"
    before = registry_path.read_bytes()

    with pytest.raises(SystemExit, match="objets JSON valides"):
        registry.main([
            "register", "linear", "demo", "DEMO", _LINEAR_PROJECT_ID,
            f"state_ids={structured_value}",
        ])

    assert registry_path.read_bytes() == before
    assert "demo" not in registry.load().get("linear", {})


def test_alias_copies_the_project_binding_without_creating_a_project(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    registry.register("youtrack", "foundry", "FOUNDRY", "0-3", ms_bundle="163-7")

    assert registry.register_alias("youtrack", "foundry", "claude-plugins") is True
    assert registry.resolve(
        "youtrack", "claude-plugins", cwd=str(tmp_path),
    ) == registry.resolve(
        "youtrack", "foundry", cwd=str(tmp_path),
    )
    assert registry.load()["youtrack"]["claude-plugins"] == {
        "key": "FOUNDRY",
        "id": "0-3",
        "ms_bundle": "163-7",
    }
    assert registry.register_alias("youtrack", "foundry", "claude-plugins") is False


def test_linear_alias_copies_a_complete_safe_binding(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    extra = _linear_binding()
    registry.register("linear", "trame", "TRAME", _LINEAR_PROJECT_ID, **extra)

    assert registry.register_alias("linear", "trame", "trame-renamed") is True
    assert registry.load()["linear"]["trame-renamed"] == registry.load()["linear"]["trame"]


@pytest.mark.parametrize("alias_source", [
    _LINEAR_PROJECT_ID,
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
])
def test_linear_alias_rejects_identifier_collision_without_writing(
    monkeypatch, tmp_path, alias_source,
):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    extra = _linear_binding()
    registry.register("linear", "trame", "TRAME", _LINEAR_PROJECT_ID, **extra)
    registry_path = state / "registry.json"
    before = registry_path.read_bytes()

    with pytest.raises(ValueError, match="binding Linear invalide"):
        registry.register_alias("linear", "trame", alias_source)

    assert registry_path.read_bytes() == before
    assert alias_source not in registry.load()["linear"]


def test_linear_alias_refresh_revalidates_target_alias_before_writing(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    extra = _linear_binding()
    registry.register("linear", "trame", "TRAME", _LINEAR_PROJECT_ID, **extra)
    data = registry.load()
    data["linear"][_LINEAR_PROJECT_ID] = {"key": "TRAME", "id": _LINEAR_PROJECT_ID}
    registry._save(data)
    registry_path = state / "registry.json"
    before = registry_path.read_bytes()

    with pytest.raises(ValueError, match="binding Linear invalide"):
        registry.register_alias("linear", "trame", _LINEAR_PROJECT_ID)

    assert registry_path.read_bytes() == before


def test_alias_refreshes_metadata_when_project_identity_matches(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    registry.register("youtrack", "foundry", "FOUNDRY", "0-3", ms_bundle="163-7")
    registry.register("youtrack", "claude-plugins", "FOUNDRY", "0-3")

    assert registry.register_alias("youtrack", "foundry", "claude-plugins") is True
    assert registry.load()["youtrack"]["claude-plugins"]["ms_bundle"] == "163-7"


def test_alias_refuses_to_overwrite_a_different_project(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    registry.register("youtrack", "foundry", "FOUNDRY", "0-3")
    registry.register("youtrack", "claude-plugins", "OTHER", "0-9")

    with pytest.raises(ValueError, match="déjà lié à OTHER"):
        registry.register_alias("youtrack", "foundry", "claude-plugins")


def _make_repo(tmp_path, name="demo"):
    repo = tmp_path / name
    repo.mkdir()

    def run(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    run("init", "-b", "main")
    run("config", "remote.origin.url", f"https://github.com/acme/{name}.git")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit",
        "--allow-empty", "-m", "init")
    return repo


def _hook_env(home, plugin_data):
    """The env Codex gives hook commands: plugin-private dirs set, skills saw none."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "PLUGIN_DATA": str(plugin_data),
        "CLAUDE_PLUGIN_DATA": str(plugin_data),
    }
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _write_registry_as_skill(monkeypatch, home, repo_name):
    """Register the repo the way a skill command does: no plugin env at all."""
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("HOME", str(home))
    registry.register("youtrack", repo_name, "DEMO", "0-1")


@pytest.mark.parametrize("remote", [
    "https://github.com/acme/trame.git",
    "https://credential-user:credential-password@github.com/acme/trame.git",
    "git@github.com:acme/trame.git",
    "ssh://git@github.com/acme/trame.git",
    "github.com/acme/trame",
])
def test_canonical_repository_identity_accepts_supported_remote_forms(remote):
    assert registry.canonical_repository_identity(remote) == "github.com/acme/trame"


@pytest.mark.parametrize("remote", [
    "ssh://work/acme/trame.git",
    "work:acme/trame.git",
])
def test_canonical_repository_identity_accepts_locally_proven_ssh_alias(
    monkeypatch, remote,
):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, stdout="host work\nhostname GitHub.com\n", stderr="",
        )

    monkeypatch.setattr(registry.subprocess, "run", run)

    assert registry.canonical_repository_identity(remote) == "github.com/acme/trame"
    assert calls == [(
        ["ssh", "-G", "-o", "CanonicalizeHostname=no", "--", "work"],
        {
            "capture_output": True,
            "text": True,
            "timeout": registry._SSH_CONFIG_TIMEOUT_SECONDS,
        },
    )]


@pytest.mark.parametrize("remote", [
    "ssh://github.com-evil/acme/trame.git",
    "git@github.com-evil:acme/trame.git",
])
def test_canonical_repository_identity_never_infers_host_from_github_resemblance(
    monkeypatch, remote,
):
    monkeypatch.setattr(
        registry.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("un hôte qualifié ne doit pas être un alias"),
    )

    assert (
        registry.canonical_repository_identity(remote)
        == "github.com-evil/acme/trame"
    )


def test_canonical_repository_identity_keeps_https_host_literal(monkeypatch):
    monkeypatch.setattr(
        registry.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("HTTPS ne doit pas consulter ssh-config"),
    )

    assert (
        registry.canonical_repository_identity(
            "https://github.com-alias/acme/trame.git",
        )
        == "github.com-alias/acme/trame"
    )


def test_canonical_repository_identity_refuses_unproven_ssh_alias(monkeypatch):
    def run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout="hostname work\n", stderr="",
        )

    monkeypatch.setattr(registry.subprocess, "run", run)

    with pytest.raises(ValueError, match="alias SSH impossible à vérifier"):
        registry.canonical_repository_identity("work:acme/trame.git")


def test_checkout_repository_identity_strips_credentials_without_dns(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0,
            stdout="https://sensitive-user:sensitive-token@github.com/Acme/Trame.git\n",
            stderr="",
        )

    monkeypatch.setattr(registry.subprocess, "run", run)

    identity = registry.checkout_repository_identity("/checkout")

    assert identity == "github.com/acme/trame"
    assert "sensitive" not in identity
    assert calls == [(
        ["git", "config", "--get", "remote.origin.url"],
        {"cwd": "/checkout", "capture_output": True, "text": True},
    )]


def test_register_persists_only_credential_free_canonical_repository(
    monkeypatch, tmp_path,
):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))

    registry.register(
        "devhub", "trame", "TRAME", "42",
        canonical_repo=(
            "https://credential-user:PLAINTEXT_SENTINEL@github.com/Acme/Trame.git"
        ),
    )

    raw = json.loads((tmp_path / "state" / "registry.json").read_text(encoding="utf-8"))
    assert raw["devhub"]["trame"]["canonical_repo"] == "github.com/acme/trame"
    assert "credential-user" not in json.dumps(raw)
    assert "PLAINTEXT_SENTINEL" not in json.dumps(raw)


def test_legacy_credentialed_registry_is_sanitized_before_display(
    monkeypatch, tmp_path, capsys,
):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    (state / "registry.json").write_text(json.dumps({
        "devhub": {
            "trame": {
                "key": "TRAME",
                "id": "42",
                "canonical_repo": (
                    "https://credential-user:PLAINTEXT_SENTINEL@github.com/Acme/Trame.git"
                ),
            },
        },
    }), encoding="utf-8")

    registry.main([])

    output = capsys.readouterr().out
    assert "github.com/acme/trame" in output
    assert "credential-user" not in output
    assert "PLAINTEXT_SENTINEL" not in output


def test_invalid_legacy_canonical_repository_fails_closed_without_echo(
    monkeypatch, tmp_path,
):
    _clear_data_env(monkeypatch)
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("FOUNDRY_DATA", str(state))
    (state / "registry.json").write_text(json.dumps({
        "devhub": {
            "trame": {
                "key": "TRAME",
                "id": "42",
                "canonical_repo": (
                    "https://credential-user:PLAINTEXT_SENTINEL@github.com/acme/trame"
                    "?token=PLAINTEXT_SENTINEL"
                ),
            },
        },
    }), encoding="utf-8")

    with pytest.raises(ValueError) as error:
        registry.load()

    assert str(error.value) == "canonical_repo invalide dans le registre Foundry"
    assert "PLAINTEXT_SENTINEL" not in str(error.value)


_MANIFEST_DIGEST = "sha256:" + "a" * 64


def _prepare_cutover(monkeypatch, tmp_path):
    _clear_data_env(monkeypatch)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    repo = _make_repo(tmp_path, "public")
    registry.register("youtrack", "public", "FOUNDRY", "0-3")
    registry.register("youtrack", "claude-plugins", "FOUNDRY", "0-3")
    extra = _linear_binding()
    extra["canonical_repo"] = "github.com/acme/public"
    registry.register("linear", "public", "PAT", _LINEAR_PROJECT_ID, **extra)
    return repo, extra


def test_repository_cutover_selects_complete_linear_binding_from_nested_checkout(
    monkeypatch, tmp_path,
):
    repo, extra = _prepare_cutover(monkeypatch, tmp_path)
    nested = repo / "plugins" / "foundry"
    nested.mkdir(parents=True)

    binding = registry.cutover_repository_tracker(
        "linear", "PAT", _LINEAR_PROJECT_ID,
        migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(nested),
    )

    assert registry.tracker_name_for_checkout(str(nested)) == "linear"
    assert registry.repository_tracker_binding(str(nested)) == binding
    assert binding.project.extra["team_id"] == extra["team_id"]
    assert binding.project.extra["state_ids"] == extra["state_ids"]
    assert registry.resolve("linear", "public", cwd=str(nested)) == binding.project
    with pytest.raises(SystemExit, match="alias de dépôt incompatible"):
        registry.resolve("linear", "other", cwd=str(nested))
    with pytest.raises(SystemExit, match="actif sur 'linear'"):
        registry.resolve("youtrack", "public", cwd=str(nested))


@pytest.mark.parametrize("payload", [
    b"{",
    b'{"version":1,"repository":"github.com/acme/public","tracker":"linear"}',
    (
        b'{"version":1,"repository":"github.com/acme/public","tracker":"linear",'
        b'"project":{"key":"PAT","id":"00000000-0000-4000-8000-000000000000"},'
        b'"registry_binding_digest":"sha256:' + b"0" * 64 + b'",'
        b'"migration_manifest_digest":"sha256:' + b"a" * 64 + b'",'
        b'"configuration_digest":"sha256:' + b"0" * 64 + b'","extra":true}'
    ),
])
def test_repository_marker_rejects_malformed_or_unknown_fields(
    monkeypatch, tmp_path, payload,
):
    _clear_data_env(monkeypatch)
    repo = _make_repo(tmp_path, "public")
    marker = repo / ".foundry"
    marker.mkdir()
    (marker / "tracker.json").write_bytes(payload)
    with pytest.raises(ValueError, match="marqueur tracker de dépôt invalide"):
        registry.repository_tracker_binding(str(repo))


def test_repository_marker_rejects_symlink_origin_mismatch_and_registry_drift(
    monkeypatch, tmp_path,
):
    repo, _extra = _prepare_cutover(monkeypatch, tmp_path)
    registry.cutover_repository_tracker(
        "linear", "PAT", _LINEAR_PROJECT_ID,
        migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(repo),
    )
    marker = repo / ".foundry" / "tracker.json"
    original = marker.read_bytes()

    other = tmp_path / "marker.json"
    other.write_bytes(original)
    marker.unlink()
    marker.symlink_to(other)
    with pytest.raises(ValueError, match="marqueur tracker de dépôt invalide"):
        registry.repository_tracker_binding(str(repo))

    marker.unlink()
    marker.write_bytes(original)
    subprocess.run(
        ["git", "-C", str(repo), "config", "remote.origin.url",
         "https://github.com/acme/other.git"],
        check=True,
    )
    with pytest.raises(ValueError, match="incompatible"):
        registry.repository_tracker_binding(str(repo))

    subprocess.run(
        ["git", "-C", str(repo), "config", "remote.origin.url",
         "https://github.com/acme/public.git"],
        check=True,
    )
    data = registry.load()
    data["linear"]["public"]["key"] = "CHANGED"
    registry._save(data)
    with pytest.raises(ValueError, match="incompatibles avec le registre"):
        registry.repository_tracker_binding(str(repo))


def test_cutover_requires_registered_target_and_preserves_historical_archive(
    monkeypatch, tmp_path,
):
    repo, _extra = _prepare_cutover(monkeypatch, tmp_path)
    data = registry.load()
    del data["linear"]
    registry._save(data)

    with pytest.raises(ValueError, match="absent du registre"):
        registry.cutover_repository_tracker(
            "linear", "PAT", _LINEAR_PROJECT_ID,
            migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(repo),
        )

    assert registry.load()["youtrack"]["public"].get("archive") is None
    assert registry.load()["youtrack"]["claude-plugins"].get("archive") is None
    assert not (repo / ".foundry" / "tracker.json").exists()


def test_cutover_archives_only_public_source_and_replays_idempotently(
    monkeypatch, tmp_path,
):
    repo, _extra = _prepare_cutover(monkeypatch, tmp_path)
    historical = _make_repo(tmp_path, "claude-plugins")
    first = registry.cutover_repository_tracker(
        "linear", "PAT", _LINEAR_PROJECT_ID,
        migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(repo),
    )
    second = registry.cutover_repository_tracker(
        "linear", "PAT", _LINEAR_PROJECT_ID,
        migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(repo),
    )

    data = registry.load()
    assert first == second
    assert data["youtrack"]["public"]["archive"] is True
    assert data["youtrack"]["claude-plugins"] == {"key": "FOUNDRY", "id": "0-3"}
    with pytest.raises(SystemExit, match="archivé"):
        registry.resolve("youtrack", "public", cwd=str(tmp_path))
    archive = registry.resolve(
        "youtrack", "claude-plugins", cwd=str(historical),
    )
    assert archive.key == "FOUNDRY"
    with pytest.raises(SystemExit, match="archive lisible"):
        registry.require_writable_project("youtrack", archive)
    with pytest.raises(ValueError, match="binding actif différent"):
        registry.cutover_repository_tracker(
            "linear", "PAT", _LINEAR_PROJECT_ID,
            migration_manifest_digest="sha256:" + "b" * 64, cwd=str(repo),
        )


def test_concurrent_conflicting_cutovers_serialize_and_refuse_loser(
    monkeypatch, tmp_path,
):
    repo, _extra = _prepare_cutover(monkeypatch, tmp_path)
    real_prepare = registry._prepare_marker
    first_prepared = threading.Event()
    release_first = threading.Event()
    second_reached_prepare = threading.Event()
    calls_lock = threading.Lock()
    calls = 0

    def controlled_prepare(root, payload):
        nonlocal calls
        with calls_lock:
            calls += 1
            call = calls
        if call == 1:
            first_prepared.set()
            assert release_first.wait(timeout=5)
        else:
            second_reached_prepare.set()
        return real_prepare(root, payload)

    monkeypatch.setattr(registry, "_prepare_marker", controlled_prepare)

    def cutover(digest):
        return registry.cutover_repository_tracker(
            "linear", "PAT", _LINEAR_PROJECT_ID,
            migration_manifest_digest=digest, cwd=str(repo),
        )

    first_digest = _MANIFEST_DIGEST
    second_digest = "sha256:" + "b" * 64
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cutover, first_digest)
        assert first_prepared.wait(timeout=5)
        second = pool.submit(cutover, second_digest)
        assert not second_reached_prepare.wait(timeout=0.25)
        assert not second.done()
        release_first.set()

        assert first.result(timeout=5).migration_manifest_digest == first_digest
        with pytest.raises(ValueError, match="binding actif différent"):
            second.result(timeout=5)

    assert calls == 1
    assert registry.repository_tracker_binding(str(repo)).migration_manifest_digest == (
        first_digest
    )


def test_cutover_refuses_ambiguous_sources_without_publishing(monkeypatch, tmp_path):
    repo, _extra = _prepare_cutover(monkeypatch, tmp_path)
    registry.register(
        "devhub", "other", "OTHER", "42",
        canonical_repo="github.com/acme/public",
    )
    before = registry.load()

    with pytest.raises(ValueError, match="bindings source actifs ambigus"):
        registry.cutover_repository_tracker(
            "linear", "PAT", _LINEAR_PROJECT_ID,
            migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(repo),
        )

    assert registry.load() == before
    assert not (repo / ".foundry" / "tracker.json").exists()


def test_interrupted_marker_publication_stays_fail_closed_and_is_recoverable(
    monkeypatch, tmp_path,
):
    repo, _extra = _prepare_cutover(monkeypatch, tmp_path)
    real_replace = registry.os.replace

    def interrupted(source, destination):
        if str(destination).endswith(".foundry/tracker.json"):
            raise OSError("simulated marker interruption")
        return real_replace(source, destination)

    monkeypatch.setattr(registry.os, "replace", interrupted)
    with pytest.raises(OSError, match="marker interruption"):
        registry.cutover_repository_tracker(
            "linear", "PAT", _LINEAR_PROJECT_ID,
            migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(repo),
        )
    assert registry.load()["youtrack"]["public"]["archive"] is True
    assert not (repo / ".foundry" / "tracker.json").exists()
    with pytest.raises(SystemExit, match="archivé"):
        registry.resolve("youtrack", "public", cwd=str(tmp_path))

    monkeypatch.setattr(registry.os, "replace", real_replace)
    recovered = registry.cutover_repository_tracker(
        "linear", "PAT", _LINEAR_PROJECT_ID,
        migration_manifest_digest=_MANIFEST_DIGEST, cwd=str(repo),
    )
    assert recovered.tracker == "linear"


def test_live_cutover_attestation_separates_private_input_and_public_redaction():
    root = Path(__file__).resolve().parents[3]
    marker = json.loads((root / ".foundry/tracker.json").read_text(encoding="utf-8"))
    operations = json.loads(
        (root / "plugins/foundry/docs/linear-cutover-operations.json").read_text(
            encoding="utf-8",
        )
    )
    public_manifest_path = (
        root / "plugins/foundry/docs/linear-selective-migration-manifest.json"
    )
    public_manifest = json.loads(public_manifest_path.read_text(encoding="utf-8"))
    attestation = operations["attestation"]
    cutover = attestation["cutover_manifest"]
    public = attestation["public_redacted_manifest"]

    assert operations["schema_version"] == 2
    assert cutover == {
        "availability": "private-operator-evidence",
        "binding": ".foundry/tracker.json#migration_manifest_digest",
        "digest": "sha256:e15d28556317cd664c9f2467cf9d69df3673315e9bb42535b545afa11208df77",
        "role": "immutable-cutover-input",
    }
    assert marker["migration_manifest_digest"] == cutover["digest"]
    assert public["digest"] == (
        "sha256:" + hashlib.sha256(public_manifest_path.read_bytes()).hexdigest()
    )
    assert public["digest"] == (
        "sha256:4589727201ae44a00d0b47c92fca38a6fcc7a5bbde7eba0e8668a07e60d721dc"
    )
    assert public["digest"] != cutover["digest"]
    assert public["derivation"] == {
        "redacted_fields": ["source.issue_url_reference"],
        "source_digest": cutover["digest"],
        "transformation": "replace-private-youtrack-url-with-archive-reference",
    }
    assert public_manifest["source"]["issue_url_reference"] == (
        "private-readable-archive"
    )

    def external_urls(value):
        if isinstance(value, str):
            return [value] if value.startswith(("http://", "https://")) else []
        if isinstance(value, list):
            return [url for item in value for url in external_urls(item)]
        if isinstance(value, dict):
            return [url for item in value.values() for url in external_urls(item)]
        return []

    assert all(
        url.startswith("https://github.com/")
        for document in (public_manifest, operations)
        for url in external_urls(document)
    )


def test_session_start_sees_skill_written_registry(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    _write_registry_as_skill(monkeypatch, tmp_path, "demo")

    r = subprocess.run(
        ["python3", os.path.join(_HOOKS, "session_start.py")],
        input=json.dumps({"cwd": str(repo), "source": "startup"}),
        env=_hook_env(tmp_path, tmp_path / "plugin-private"),
        capture_output=True, text=True, check=True)

    out = json.loads(r.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "DEMO" in ctx and "youtrack" in ctx


def test_guard_bash_sees_skill_written_registry(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    _write_registry_as_skill(monkeypatch, tmp_path, "demo")

    r = subprocess.run(
        ["python3", os.path.join(_HOOKS, "guard_bash.py")],
        input=json.dumps({"cwd": str(repo),
                          "tool_input": {"command": "git push origin main"}}),
        env=_hook_env(tmp_path, tmp_path / "plugin-private"),
        capture_output=True, text=True, check=True)

    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
