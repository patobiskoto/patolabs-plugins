"""PAT-296: the live DevHub suite refuses what it cannot show to be disposable."""
from __future__ import annotations

import re

import pytest

import devhub_live_gate as gate
from foundry import config

TOKEN = "t" * 32
SECRET = "s" * 40


def _env(**overrides):
    base = {
        gate.ENV_URL: "http://127.0.0.1:3119",
        gate.ENV_CONFIRM: gate.CONFIRM,
        gate.ENV_TOKEN: TOKEN,
        gate.ENV_PROOF_SECRET: SECRET,
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def test_complete_opt_in_yields_a_config_without_leaking_secrets():
    cfg, reason = gate.read_environment(_env(**{gate.ENV_URL: "http://127.0.0.1:3119/"}))
    assert reason is None and cfg.url == "http://127.0.0.1:3119"
    assert TOKEN not in repr(cfg) and SECRET not in repr(cfg)


@pytest.mark.parametrize("missing", [
    gate.ENV_URL, gate.ENV_CONFIRM, gate.ENV_TOKEN, gate.ENV_PROOF_SECRET,
])
def test_a_missing_variable_skips_with_a_reason(missing):
    cfg, reason = gate.read_environment(_env(**{missing: None}))
    assert cfg is None and missing in reason


def test_empty_environment_skips_and_a_wrong_phrase_skips():
    assert gate.read_environment({})[0] is None
    cfg, reason = gate.read_environment(_env(**{gate.ENV_CONFIRM: "yes"}))
    assert cfg is None and gate.ENV_CONFIRM in reason and "yes" not in reason


@pytest.mark.parametrize("url", [
    "http://devhub.example.test", "ftp://127.0.0.1", "http://localhost:3000",
    "https://devhub.example.test/?x=1", "not a url",
])
def test_invalid_or_cleartext_remote_urls_are_refused(url):
    with pytest.raises(gate.GateRefusal):
        gate.read_environment(_env(**{gate.ENV_URL: url}))


def test_short_credentials_are_refused():
    with pytest.raises(gate.GateRefusal):
        gate.read_environment(_env(**{gate.ENV_TOKEN: "short"}))


def test_the_everyday_origin_and_credentials_are_refused():
    with pytest.raises(gate.GateRefusal, match="origin"):
        gate.read_environment(_env(DEVHUB_URL="http://127.0.0.1:3119"))
    with pytest.raises(gate.GateRefusal, match="origin"):
        gate.read_environment(_env(DEVHUB_URL="garbage"))
    with pytest.raises(gate.GateRefusal, match="credential"):
        gate.read_environment(_env(DEVHUB_TRACKER_TOKEN=TOKEN))
    with pytest.raises(gate.GateRefusal, match="credential"):
        gate.read_environment(_env(DEVHUB_TRACKER_PROOF_SECRET=SECRET))
    cfg, _ = gate.read_environment(_env(DEVHUB_URL="https://prod.example.test"))
    assert cfg is not None


def test_the_tracker_is_built_without_reading_any_configuration(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("configuration must not be read")

    monkeypatch.setattr(config, "require", forbidden)
    monkeypatch.setattr(config, "require_public", forbidden)
    cfg, _ = gate.read_environment(_env())
    tracker = gate.build_tracker(cfg)
    assert tracker.url == cfg.url and tracker.token == TOKEN


class _Catalog:
    def __init__(self, keys=(), error=None):
        self.keys, self.error = keys, error

    def list_project_catalog(self):
        if self.error:
            raise self.error
        return [{"key": key} for key in self.keys]


def test_instance_marker_accepts_only_suite_projects():
    gate.assert_disposable_instance(_Catalog([]))
    gate.assert_disposable_instance(_Catalog([f"{gate.KEY_PREFIX}ABC12345"]))
    with pytest.raises(gate.GateRefusal, match="not a disposable"):
        gate.assert_disposable_instance(_Catalog([f"{gate.KEY_PREFIX}ABC12345", "PAT"]))


def test_instance_marker_fails_closed_on_an_unreadable_catalogue_without_details():
    with pytest.raises(gate.GateRefusal) as caught:
        gate.assert_disposable_instance(_Catalog(error=RuntimeError("secret detail")))
    assert "secret detail" not in str(caught.value)


def test_new_project_keys_are_valid_prefixed_and_distinct():
    keys = {gate.new_project_key() for _ in range(20)}
    assert len(keys) == 20
    assert all(re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", key) for key in keys)
    assert all(key.startswith(gate.KEY_PREFIX) for key in keys)
