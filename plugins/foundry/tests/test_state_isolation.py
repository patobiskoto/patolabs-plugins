"""PAT-104: the suite cannot reach the maintainer's real Foundry state directory."""
import os
import shutil
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


def test_the_stat_guard_keeps_the_os_support_sets_so_shutil_copies_symlinks(tmp_path):
    """PAT-128 review: the wrappers must be members of the ``os.supports_*`` sets that held the originals, or
    ``shutil.copystat``/``copy2`` with ``follow_symlinks=False`` pick ``_nop`` and fail with AttributeError."""
    (tmp_path / "t1").write_text("a", encoding="utf-8")
    (tmp_path / "t2").write_text("b", encoding="utf-8")
    os.symlink(tmp_path / "t1", tmp_path / "l1")
    os.symlink(tmp_path / "t2", tmp_path / "l2")
    shutil.copystat(tmp_path / "l1", tmp_path / "l2", follow_symlinks=False)
    os.unlink(tmp_path / "l2")
    shutil.copy2(tmp_path / "l1", tmp_path / "l2", follow_symlinks=False)
    assert os.path.islink(tmp_path / "l2")
    assert os.stat in os.supports_dir_fd and os.stat in os.supports_fd and os.stat in os.supports_follow_symlinks
    assert os.lstat in os.supports_dir_fd and os.lstat not in os.supports_fd


def test_the_stat_guard_never_raises_on_its_own(tmp_path, monkeypatch):
    """A relative path with a deleted cwd, or with ``dir_fd``, is not judged against the cwd (dir_fd-relative paths
    are not covered: documented in conftest)."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    assert conftest._is_real_state_path("config.env") is False
    with pytest.raises(FileNotFoundError):  # the call's own error, not the guard's
        os.stat("config.env")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x").write_text("x", encoding="utf-8")
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        assert os.stat("x", dir_fd=fd).st_size == 1
        assert conftest._is_real_state_path("x", dir_fd=fd) is False
    finally:
        os.close(fd)
