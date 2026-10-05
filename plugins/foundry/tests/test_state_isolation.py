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
