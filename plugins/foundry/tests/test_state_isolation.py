"""PAT-104: the suite cannot reach the maintainer's real Foundry state directory."""
import os
from pathlib import Path

import pytest

import conftest
from foundry import config, registry


def test_every_state_resolver_lands_in_the_sandbox(tmp_path_factory):
    home = Path(os.environ["HOME"])
    conftest.assert_state_resolvers_isolated(home)
    assert home != conftest._REAL_HOME
    assert Path(registry.data_dir()) == home / ".config" / "foundry"
    for name in conftest._STATE_ENV_OVERRIDES:
        assert name not in os.environ


def test_install_marker_and_stores_live_under_the_sandboxed_home():
    home = Path(os.environ["HOME"])
    assert config.install_marker_path().is_relative_to(home)
    assert Path(registry.data_dir()).is_relative_to(home)


def test_real_state_directory_is_unreachable():
    real = conftest._REAL_HOME / ".config" / "foundry"
    # Read-only probes of paths that cannot exist: if the guard failed they would
    # merely raise FileNotFoundError, never create anything in the real directory.
    for action in (
        lambda: os.listdir(real / "pat-104-no-such-store"),
        lambda: open(real / "pat-104-no-such-file"),
    ):
        with pytest.raises(RuntimeError, match="PAT-104 guard"):
            action()
    # Mutating events are exercised against the audit function directly, never for real.
    for event in ("os.mkdir", "os.rename", "os.remove", "shutil.rmtree"):
        with pytest.raises(RuntimeError, match="PAT-104 guard"):
            conftest._audit_real_state(event, (str(real / "acceptance-proofs" / "x"),))


def test_guard_rejects_a_resolver_pointing_at_the_real_home(monkeypatch):
    monkeypatch.setenv("FOUNDRY_DATA", str(conftest._REAL_HOME / ".config" / "foundry"))
    with pytest.raises(AssertionError, match="PAT-104"):
        conftest.assert_state_resolvers_isolated(Path(os.environ["HOME"]))


def test_a_stat_of_the_real_state_is_refused_too(tmp_path):
    """PAT-128: looking at the real config is a dependence on the maintainer's machine, even if nothing is read or written."""
    real = conftest._REAL_HOME / ".config" / "foundry"
    for action in (
        lambda: os.stat(real / "config.env"),
        lambda: os.lstat(real / "config.env"),
        lambda: (real / "config.env").exists(),
        lambda: os.path.exists(real / "registry.json"),
        lambda: (conftest._REAL_HOME / ".config" / "orfeo-poc" / "youtrack.env").is_file(),
    ):
        with pytest.raises(RuntimeError, match="PAT-104 guard"):
            action()
    assert os.stat(tmp_path).st_mode and os.lstat(tmp_path).st_mode  # an ordinary path is untouched
    assert not (Path(os.environ["HOME"]) / ".config" / "foundry" / "config.env").exists()


def test_the_dev_config_files_are_looked_for_under_the_sandboxed_home_only(monkeypatch):
    """The path the doctor tests of PAT-19 v5 stat-ed: with a HOME that has no Foundry config nothing is found, nothing real is looked at."""
    assert config._load_dev_files() == {}
    home = Path(os.environ["HOME"])
    assert all(Path(p).is_relative_to(home) for p in config._dev_files())
