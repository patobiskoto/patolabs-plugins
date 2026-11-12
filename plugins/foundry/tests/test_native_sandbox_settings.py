"""PLP-381: tests of the two functions of ``foundry.local_first_runner`` that live code uses.

``campaign_effects.py`` imports ``native_sandbox_settings`` and ``with_native_sandbox`` from
``local_first_runner``; the rest of that workstream is frozen (``frozen`` marker, PLP-364,
PLP-381) but these tests stay in the required CI run. Moved unchanged from
``test_local_first_native_sandbox.py``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from foundry import local_first_runner as lfr

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"


def _zone(tmp_path, in_home=False):
    home = tmp_path / "home"
    work = (home / "work") if in_home else tmp_path / "work"
    attempt = work / "private-a" / "a"
    for d in (home / ".ssh", home / ".config" / "foundry", attempt / "bundle", attempt / "scratch",
              tmp_path / "state", tmp_path / "repo"):
        d.mkdir(parents=True, exist_ok=True)
    (home / ".netrc").write_text("x")  # a FILE entry of the sensitive list
    deny = [home / ".ssh", home / ".netrc", home / ".config" / "foundry", tmp_path / "state", tmp_path / "repo"]
    return home, work, attempt, deny


def test_the_settings_use_absolute_paths_and_deny_home_and_work_root(tmp_path):
    home, work, attempt, deny = _zone(tmp_path)
    s = lfr.native_sandbox_settings(attempt_dir=attempt, work_root=work, home=home, deny_read=deny,
                                    allow_write=[tmp_path / "extra"])
    real = os.path.realpath
    assert s["sandbox"] == {
        "enabled": True, "allowUnsandboxedCommands": False, "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True,
        "filesystem": {"denyRead": [real(home), real(work), real(tmp_path / "state"), real(tmp_path / "repo")],
                       "allowRead": [real(attempt)], "allowWrite": [real(attempt), real(tmp_path / "extra")]}}
    p = s["permissions"]
    assert p["blockReadsOutsideWorkingDirectories"] is True and p["additionalDirectories"] == [real(attempt)]
    assert p["allow"] == [f"Read(//{real(attempt).lstrip('/')}/**)", f"Edit(//{real(attempt).lstrip('/')}/**)"]
    for tool in ("Read", "Edit"):  # the home, its sensitive entries and the other denied roots, not the attempt
        for path in (home, home / ".ssh", tmp_path / "state", tmp_path / "repo"):
            assert f"{tool}(//{real(path).lstrip('/')}/**)" in p["deny"]
        assert f"{tool}(//{real(work).lstrip('/')}/**)" not in p["deny"]  # it holds the attempt: deny beats allow
        for rule in (f"{tool}(//{real(home / '.netrc').lstrip('/')})", f"{tool}(//{real(home / '.netrc').lstrip('/')}/**)"):
            assert rule in p["deny"]  # N1: a FILE entry is denied bare and with /** (p/** covers only a directory's content)
    flat = json.dumps(s)
    assert '"."' not in flat and '"./' not in flat and "~" not in flat
    for key in ("denyRead", "allowRead", "allowWrite"):
        assert all(os.path.isabs(x) for x in s["sandbox"]["filesystem"][key])
    assert json.loads(json.dumps(s)) == s


def test_an_attempt_inside_the_home_gets_no_home_permission_rule_but_keeps_the_narrower_sandbox_allow(tmp_path):
    home, work, attempt, deny = _zone(tmp_path, in_home=True)
    s = lfr.native_sandbox_settings(attempt_dir=attempt, work_root=work, home=home, deny_read=deny)
    real = os.path.realpath
    assert real(home) in s["sandbox"]["filesystem"]["denyRead"] and real(attempt) in s["sandbox"]["filesystem"]["allowRead"]
    deny_rules = s["permissions"]["deny"]
    assert f"Read(//{real(home).lstrip('/')}/**)" not in deny_rules  # it would deny the attempt too
    assert f"Read(//{real(home / '.ssh').lstrip('/')}/**)" in deny_rules  # the sensitive entries stay denied
    assert f"Read(//{real(home / '.netrc').lstrip('/')})" in deny_rules  # a file entry too, in both forms
    assert f"Edit(//{real(home / '.netrc').lstrip('/')}/**)" in deny_rules
    assert not any(rule.startswith(("Read(//", "Edit(//")) and real(attempt).lstrip("/") in rule
                   for rule in deny_rules)


def test_the_settings_refuse_an_attempt_outside_the_work_root(tmp_path):
    home, work, attempt, _ = _zone(tmp_path)
    with pytest.raises(lfr.RunnerError, match="inside the work root"):
        lfr.native_sandbox_settings(attempt_dir=tmp_path / "elsewhere", work_root=work, home=home)
    with pytest.raises(lfr.RunnerError, match="absolute home"):
        lfr.native_sandbox_settings(attempt_dir=attempt, work_root=work, home="relative")


def test_the_argv_changes_only_by_the_mode_and_the_settings_and_not_at_all_without_the_key():
    v4 = json.loads((QUALIFICATION / "pat-19-campaign-v4.json").read_text("utf-8"))
    for name in ("cloud_implementer_current", "cloud_reviewer"):
        argv = v4["drivers"][name]["argv"]
        before = list(argv)
        out = lfr.with_native_sandbox(argv, {"a": 1})
        assert argv == before  # the config is never mutated
        i = before.index("--permission-mode")
        assert before[i + 1] == "bypassPermissions"
        assert out[:i] == before[:i] and out[i:len(before) - 2] == before[i + 2:]  # the rest keeps its order
        assert out[-4:] == ["--permission-mode", "dontAsk", "--settings", '{"a":1}']
        assert "bypassPermissions" not in out and out.count("--permission-mode") == 1
    assert lfr.with_native_sandbox(["x", "-p"], {})[:2] == ["x", "-p"]  # a driver with no mode gets one
