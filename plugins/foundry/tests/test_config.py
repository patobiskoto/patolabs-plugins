import stat
import secrets
import shutil
import subprocess
import sys
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry import config, configure, registry


def test_direct_environment_wins_over_claude_option_and_file(monkeypatch, tmp_path):
    path = tmp_path / "config.env"
    path.write_text("YOUTRACK_URL=https://file.example\n", encoding="utf-8")
    monkeypatch.setenv("FOUNDRY_CONFIG", str(path))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_YOUTRACK_URL", "https://claude.example")
    monkeypatch.setenv("YOUTRACK_URL", "https://env.example")

    assert config.get("YOUTRACK_URL") == "https://env.example"


def test_token_prefers_claude_option_then_keychain(monkeypatch):
    monkeypatch.delenv("YOUTRACK_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_YOUTRACK_TOKEN", "claude-token")
    monkeypatch.setattr(config, "_keychain_token", lambda: "keychain-token")
    assert config.get("YOUTRACK_TOKEN") == "claude-token"

    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_YOUTRACK_TOKEN")
    assert config.get("YOUTRACK_TOKEN") == "keychain-token"


def test_write_settings_refuses_secrets_and_sets_private_permissions(monkeypatch, tmp_path):
    path = tmp_path / "config.env"
    monkeypatch.setenv("FOUNDRY_CONFIG", str(path))

    with pytest.raises(ValueError, match="secrets"):
        config.write_settings({"YOUTRACK_TOKEN": "never-write-me"})
    with pytest.raises(ValueError, match="invalide"):
        config.write_settings({"YOUTRACK_URL": "https://safe.example\nINJECTED=value"})

    assert config.write_settings({"YOUTRACK_URL": "https://tracker.example"}) == path
    assert path.read_text(encoding="utf-8") == "YOUTRACK_URL=https://tracker.example\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_store_token_uses_native_keychain_api_without_subprocess_or_argv(monkeypatch):
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(config.shutil, "which", lambda _name: "/usr/bin/security")
    written = []

    monkeypatch.setattr(config, "_store_secret_macos", lambda account, value: written.append((account, value)))
    monkeypatch.setattr(config, "_keychain_secret", lambda _account: "secret-value")
    config.store_token("secret-value")

    assert written == [("YOUTRACK_TOKEN", "secret-value")]


def test_devhub_secrets_use_distinct_keychain_accounts(monkeypatch):
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(config.shutil, "which", lambda _name: "/usr/bin/security")
    calls = []

    monkeypatch.setattr(config, "_store_secret_macos", lambda account, value: calls.append((account, value)))
    monkeypatch.setattr(
        config, "_keychain_secret",
        lambda account: {
            "DEVHUB_TRACKER_TOKEN": "tracker-secret",
            "DEVHUB_TRACKER_PROOF_SECRET": "proof-secret",
        }[account],
    )
    config.store_secret("DEVHUB_TRACKER_TOKEN", "tracker-secret")
    config.store_secret("DEVHUB_TRACKER_PROOF_SECRET", "proof-secret")

    assert calls == [
        ("DEVHUB_TRACKER_TOKEN", "tracker-secret"),
        ("DEVHUB_TRACKER_PROOF_SECRET", "proof-secret"),
    ]


def test_store_secret_rejects_empty_value_before_host_write(monkeypatch):
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(config.shutil, "which", lambda _name: "/usr/bin/security")
    monkeypatch.setattr(
        config, "_store_secret_macos",
        lambda *_args: pytest.fail("empty credential must not reach the host"),
    )

    with pytest.raises(ValueError, match="vide"):
        config.store_secret("DEVHUB_TRACKER_TOKEN", "")


def test_store_secret_redacts_native_host_failure(monkeypatch):
    secret = "secret-that-must-not-escape"
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(config.shutil, "which", lambda _name: "/usr/bin/security")

    def fail(_account, _value):
        raise RuntimeError(f"native failure while holding {secret}")

    monkeypatch.setattr(config, "_store_secret_macos", fail)
    with pytest.raises(RuntimeError) as error:
        config.store_secret("DEVHUB_TRACKER_TOKEN", secret)

    assert str(error.value) == "Échec d'écriture dans le keychain macOS."
    assert secret not in str(error.value)


def test_store_secret_rejects_silent_host_readback_mismatch(monkeypatch):
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(config.shutil, "which", lambda _name: "/usr/bin/security")
    monkeypatch.setattr(config, "_store_secret_macos", lambda *_args: None)
    monkeypatch.setattr(config, "_keychain_secret", lambda _account: None)

    with pytest.raises(RuntimeError, match="Échec d'écriture"):
        config.store_secret("DEVHUB_TRACKER_TOKEN", "expected-secret")


def test_keychain_reader_uses_stable_system_executable(monkeypatch):
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(config.shutil, "which", lambda _name: "/usr/bin/security")
    calls = []

    def legacy_reader(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="legacy-value\n")

    monkeypatch.setattr(config.subprocess, "run", legacy_reader)

    assert config._keychain_secret("YOUTRACK_TOKEN") == "legacy-value"
    assert calls[0][0] == [
        "/usr/bin/security", "find-generic-password", "-s", "patolabs.foundry",
        "-a", "YOUTRACK_TOKEN", "-w",
    ]
    assert calls[0][1]["timeout"] == 5


def test_devhub_secrets_resolve_from_their_own_keychain_accounts(monkeypatch):
    monkeypatch.delenv("DEVHUB_TRACKER_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_DEVHUB_TRACKER_TOKEN", raising=False)
    monkeypatch.setattr(config, "_load_dev_files", lambda: {})
    seen = []

    def keychain(account):
        seen.append(account)
        return {
            "DEVHUB_TRACKER_TOKEN": "tracker-value",
            "DEVHUB_TRACKER_PROOF_SECRET": "proof-value",
        }.get(account)

    monkeypatch.setattr(config, "_keychain_secret", keychain)

    assert config.get("DEVHUB_TRACKER_TOKEN") == "tracker-value"
    assert config.get("DEVHUB_TRACKER_PROOF_SECRET") == "proof-value"
    assert seen == ["DEVHUB_TRACKER_TOKEN", "DEVHUB_TRACKER_PROOF_SECRET"]


def test_install_marker_ignores_plugin_private_data_dirs(monkeypatch, tmp_path):
    marker = tmp_path / "shared" / "install.json"
    monkeypatch.setattr(config, "install_marker_path", lambda: marker)
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "foundry-private"))
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-private"))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "claude-private"))

    assert configure._record_installation() == marker
    assert marker.is_file()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600


def test_data_dir_priority(monkeypatch):
    # the full sharing contract (plugin-private vars ignored) is pinned in
    # test_registry_sharing.py; this only pins the explicit override
    for key in ("FOUNDRY_DATA", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FOUNDRY_DATA", "/foundry")

    assert registry.data_dir() == "/foundry"


def test_public_file_fallback_does_not_invoke_bulk_secret_parser(monkeypatch, tmp_path):
    config_path = tmp_path / "config.env"
    config_path.write_text(
        "DEVHUB_TRACKER_TOKEN=PLAINTEXT_SENTINEL\n"
        "FOUNDRY_TRACKER=devhub\n"
        "DEVHUB_URL=https://devhub.example\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_CONFIG", str(config_path))
    for key in (
        "FOUNDRY_TRACKER", "DEVHUB_URL",
        "CLAUDE_PLUGIN_OPTION_FOUNDRY_TRACKER",
        "CLAUDE_PLUGIN_OPTION_DEVHUB_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        config, "_load_dev_files",
        lambda: pytest.fail("public resolution must not bulk-parse config.env"),
    )

    assert config.tracker_name() == "devhub"
    assert config.require_public("DEVHUB_URL") == "https://devhub.example"


def test_public_resolution_preserves_cross_host_precedence(monkeypatch, tmp_path):
    config_path = tmp_path / "config.env"
    config_path.write_text("FOUNDRY_TRACKER=file-provider\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_CONFIG", str(config_path))
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_FOUNDRY_TRACKER", "claude-provider")
    monkeypatch.setenv("FOUNDRY_TRACKER", "direct-provider")

    assert config.tracker_name() == "direct-provider"
    monkeypatch.delenv("FOUNDRY_TRACKER")
    assert config.tracker_name() == "claude-provider"
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_FOUNDRY_TRACKER")
    assert config.tracker_name() == "file-provider"


@pytest.mark.parametrize("unsafe_url", [
    "https://credential-user:PLAINTEXT_SENTINEL@devhub.example",
    "http://devhub.example",
    "https://devhub.example?token=PLAINTEXT_SENTINEL",
    "https://devhub.example#PLAINTEXT_SENTINEL",
])
def test_configure_set_rejects_unsafe_devhub_url_before_persistence(
    monkeypatch, tmp_path, unsafe_url,
):
    config_path = tmp_path / "config.env"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_CONFIG", str(config_path))

    with pytest.raises(ValueError) as error:
        configure.set_values(None, "devhub", "github", unsafe_url)

    assert not config_path.exists()
    assert "PLAINTEXT_SENTINEL" not in str(error.value)


@pytest.mark.parametrize(("value", "stored"), [
    ("https://devhub.example/", "https://devhub.example"),
    ("http://127.0.0.1:3000/", "http://127.0.0.1:3000"),
    ("http://[::1]:3000/", "http://[::1]:3000"),
])
def test_configure_set_persists_valid_devhub_transport(
    monkeypatch, tmp_path, value, stored,
):
    config_path = tmp_path / "config.env"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_CONFIG", str(config_path))

    configure.set_values(None, "devhub", "github", value)

    assert f"DEVHUB_URL={stored}\n" in config_path.read_text(encoding="utf-8")


def test_configure_show_redacts_preexisting_credentialed_devhub_url(
    monkeypatch, tmp_path, capsys,
):
    config_path = tmp_path / "config.env"
    config_path.write_text(
        "FOUNDRY_TRACKER=devhub\n"
        "DEVHUB_URL=https://credential-user:PLAINTEXT_SENTINEL@devhub.example\n"
        "DEVHUB_TRACKER_TOKEN=PLAINTEXT_TOKEN_SENTINEL\n"
        "DEVHUB_TRACKER_PROOF_SECRET=PLAINTEXT_PROOF_SENTINEL\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_CONFIG", str(config_path))
    monkeypatch.setattr(config, "_keychain_secret", lambda _account: None)

    configure.show()

    output = capsys.readouterr().out
    assert '"DEVHUB_URL": "invalid"' in output
    assert "credential-user" not in output
    assert "PLAINTEXT_SENTINEL" not in output
    assert "PLAINTEXT_TOKEN_SENTINEL" not in output
    assert "PLAINTEXT_PROOF_SENTINEL" not in output


def test_configure_set_refuses_to_rewrite_existing_plaintext_secrets(
    monkeypatch, tmp_path,
):
    config_path = tmp_path / "config.env"
    original = (
        "DEVHUB_TRACKER_TOKEN=PLAINTEXT_SENTINEL\n"
        "DEVHUB_URL=https://devhub.example\n"
    )
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FOUNDRY_CONFIG", str(config_path))

    with pytest.raises(ValueError) as error:
        configure.set_values(None, "devhub", "github")

    assert config_path.read_text(encoding="utf-8") == original
    assert "DEVHUB_TRACKER_TOKEN" in str(error.value)
    assert "PLAINTEXT_SENTINEL" not in str(error.value)


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="requires the real macOS keychain")
def test_macos_configure_credential_round_trip_replaces_and_deletes_in_isolated_keychain(
    monkeypatch, tmp_path, capsys,
):
    """Exercise public CLI dispatch, native storage, resolver and cleanup on macOS.

    This deliberately uses a new temporary keychain and unique service namespace.
    It never touches the login keychain or an existing Foundry item. Generated
    credentials move only through mocked invisible input or subprocess stdin,
    never argv. A genuinely different Python executable proves upgrade safety.
    """
    security = shutil.which("security")
    if not security:
        pytest.skip("security(1) is unavailable")
    keychain_path = tmp_path / "foundry-77.keychain-db"
    service = f"test.foundry-77.{secrets.token_hex(12)}"
    first = f"foundry77-{secrets.token_urlsafe(24)}"
    second = f"foundry77-{secrets.token_urlsafe(24)}"
    account = "DEVHUB_TRACKER_PROOF_SECRET"
    other_python = _distinct_python_executable()
    current_real = Path(sys.executable).resolve(strict=True)
    other_real = Path(other_python).resolve(strict=True)
    assert current_real != other_real
    assert (current_real.stat().st_dev, current_real.stat().st_ino) != (
        other_real.stat().st_dev, other_real.stat().st_ino,
    )

    created = subprocess.run(
        [security, "create-keychain", "-p", "", str(keychain_path)],
        capture_output=True, text=True, timeout=10,
    )
    if created.returncode != 0:
        pytest.skip("the temporary keychain could not be created on this macOS host")
    try:
        monkeypatch.setattr(config, "_TEST_KEYCHAIN_PATH", str(keychain_path))
        monkeypatch.setattr(config, "_KEYCHAIN_SERVICE", service)
        monkeypatch.setenv("FOUNDRY_CONFIG", str(tmp_path / "missing-config.env"))
        monkeypatch.delenv(account, raising=False)
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{account}", raising=False)
        monkeypatch.setattr(configure, "_record_installation", lambda: tmp_path / "install.json")

        monkeypatch.setattr(configure.getpass, "getpass", lambda _prompt: first)
        configure.main(["credential", "--name", account])
        first_output = capsys.readouterr()
        assert first not in first_output.out + first_output.err
        assert config.get(account) == first
        _assert_fresh_process_resolves_keychain_secret(
            other_python, keychain_path, service, account, first,
        )

        acl = subprocess.run(
            [security, "dump-keychain", "-a", str(keychain_path)],
            capture_output=True, text=True, timeout=10,
        )
        assert acl.returncode == 0, acl.stderr
        assert config._SECURITY_TOOL in acl.stdout
        assert first not in acl.stdout + acl.stderr

        _configure_credential_in_process(
            other_python, keychain_path, service, account, second,
        )
        assert config.get(account) == second
        _assert_fresh_process_resolves_keychain_secret(
            sys.executable, keychain_path, service, account, second,
        )

        deleted = subprocess.run(
            [security, "delete-generic-password", "-s", service, "-a", account,
             str(keychain_path)],
            capture_output=True, text=True, timeout=10,
        )
        assert deleted.returncode == 0
        assert config.get(account) is None

        # Seed a valid item through security's interactive interpreter. The
        # complete command (including generated data) travels only on stdin;
        # it is never a process argument. This pins legacy ACL compatibility.
        legacy = secrets.token_hex(24)
        legacy_command = (
            f"add-generic-password -s {service} -a {account} -w {legacy} "
            f"{keychain_path}\n"
        )
        legacy_write = subprocess.run(
            [security, "-i"], input=legacy_command,
            capture_output=True, text=True, timeout=10,
        )
        assert legacy_write.returncode == 0, legacy_write.stderr
        assert legacy not in legacy_write.stdout + legacy_write.stderr
        assert config.get(account) == legacy
        _assert_fresh_process_resolves_keychain_secret(
            other_python, keychain_path, service, account, legacy,
        )

        replacement = f"foundry77-{secrets.token_urlsafe(24)}"
        _configure_credential_in_process(
            other_python, keychain_path, service, account, replacement,
        )
        assert config.get(account) == replacement
        _assert_fresh_process_resolves_keychain_secret(
            sys.executable, keychain_path, service, account, replacement,
        )
    finally:
        # security delete-keychain removes the disposable keychain even when an
        # assertion fails; it cannot address the user's default login keychain.
        subprocess.run(
            [security, "delete-keychain", str(keychain_path)],
            capture_output=True, text=True, timeout=10,
        )


def _distinct_python_executable() -> str:
    """Return a runnable Python with an executable identity unlike this test host."""
    current = Path(sys.executable).resolve(strict=True)
    current_identity = (current.stat().st_dev, current.stat().st_ino)
    candidates = (
        Path("/opt/homebrew/bin/python3"),
        Path.home() / ".local/bin/python3.13",
        Path("/usr/bin/python3"),
    )
    for candidate in candidates:
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        real = candidate.resolve(strict=True)
        if (real.stat().st_dev, real.stat().st_ino) == current_identity:
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import sys"],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0:
            return str(candidate)
    pytest.skip("a second real Python executable is unavailable")


def _configure_credential_in_process(
    executable: str, keychain_path, service: str, account: str, value: str,
) -> None:
    """Run public configure in another Python; pass the credential only on stdin."""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    tooling = Path(__file__).resolve().parents[1] / "tooling"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tooling)
    environment["FOUNDRY_CONFIG"] = str(Path(keychain_path).with_suffix(".missing.env"))
    environment.pop(account, None)
    environment.pop(f"CLAUDE_PLUGIN_OPTION_{account}", None)
    probe = (
        "from foundry import config, configure; import getpass, hashlib, sys; "
        f"config._TEST_KEYCHAIN_PATH = {str(keychain_path)!r}; "
        f"config._KEYCHAIN_SERVICE = {service!r}; "
        "value = sys.stdin.read(); "
        "configure._record_installation = lambda: None; "
        "getpass.getpass = lambda _prompt: value; "
        f"configure.main(['credential', '--name', {account!r}]); "
        f"resolved = config.get({account!r}); "
        f"assert resolved is not None and hashlib.sha256(resolved.encode()).hexdigest() == {digest!r}"
    )
    args = [executable, "-c", probe]
    assert all(value not in argument for argument in args)
    result = subprocess.run(
        args, input=value, env=environment,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert value not in result.stdout + result.stderr


def _assert_fresh_process_resolves_keychain_secret(
    executable: str, keychain_path, service: str, account: str, expected: str,
) -> None:
    """Verify resolver behavior after configure's process has exited, without outputting it."""
    digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    tooling = Path(__file__).resolve().parents[1] / "tooling"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tooling)
    probe = (
        "from foundry import config; import hashlib; "
        f"config._TEST_KEYCHAIN_PATH = {str(keychain_path)!r}; "
        f"config._KEYCHAIN_SERVICE = {service!r}; "
        f"value = config.get({account!r}); "
        f"assert value is not None and hashlib.sha256(value.encode()).hexdigest() == {digest!r}"
    )
    args = [executable, "-c", probe]
    assert all(expected not in argument for argument in args)
    result = subprocess.run(
        args, env=environment,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert expected not in result.stdout + result.stderr
