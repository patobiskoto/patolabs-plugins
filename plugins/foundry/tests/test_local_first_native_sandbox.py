"""PAT-124: the native Bash sandbox for the cloud drivers of the PAT-19 launcher, its coupling with audit revision 2,
and the bounded real trial tool (parsing and rendering only here).

Everything is offline and fake: no `claude`, no cloud arm, no model, no `lms`, never the real HOME or
``~/.config/foundry`` (the home is a directory of the test). Frozen v1 to v4 configs are read, never edited."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from foundry import local_first_native_trial as trial
from foundry import local_first_runner as lfr
from test_local_first_audit_revision import HOST, Layout, R2, _config
from test_local_first_exploration_runner import PLAN, _envelope, make_runner, v2_campaign
from test_local_first_corpus import _make_repo
from test_local_first_runner import results

QUALIFICATION = Path(__file__).resolve().parents[1] / "docs" / "qualification"
KEY = lfr.NATIVE_SANDBOX_KEY
V5 = "pat-19-protocol-v5"


def _v5(tmp_path, **iso):
    return _config(tmp_path, 2, protocol=V5, **iso)


# ------------------------------------------------------------------------------- the key and the loader

def test_the_key_is_refused_before_v5_and_by_a_missing_or_unknown_protocol(tmp_path):
    for v in (1, 2, 3, 4):
        config = json.loads((QUALIFICATION / f"pat-19-campaign-v{v}.json").read_text("utf-8"))
        assert KEY not in (config.get("isolation") or {}), v
        with pytest.raises(lfr.RunnerError, match=f"{KEY} is accepted only under a protocol after v4"):
            lfr.load_campaign(_config(tmp_path, v, **{KEY: True}))
    for protocol in (None, "", "pat-19-protocol-v4x", "other", "pat-19-protocol-v0"):
        path = _config(tmp_path, 2, protocol="x", **{KEY: True})
        data = json.loads(path.read_text("utf-8"))
        data.pop("protocol") if protocol is None else data.update(protocol=protocol)
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(lfr.RunnerError, match="after v4"):
            lfr.load_campaign(path)
    for protocol in (V5, "pat-19-protocol-v12"):
        assert lfr.load_campaign(_config(tmp_path, 2, protocol=protocol, **{KEY: True}))["isolation"][KEY] is True
    for bad in (1, "true", None):
        with pytest.raises(lfr.RunnerError, match="must be a boolean"):
            lfr.load_campaign(_v5(tmp_path, **{KEY: bad}))


def test_audit_revision_2_is_accepted_only_together_with_the_native_sandbox(tmp_path):
    with pytest.raises(lfr.RunnerError, match="only together with isolation.cloud_native_sandbox"):
        lfr.load_campaign(_v5(tmp_path, audit_revision=R2))
    with pytest.raises(lfr.RunnerError, match="only together with"):
        lfr.load_campaign(_v5(tmp_path, audit_revision=R2, **{KEY: False}))
    assert lfr.load_campaign(_v5(tmp_path, audit_revision=R2, **{KEY: True}))["isolation"]["audit_revision"] == R2
    assert lfr.load_campaign(_v5(tmp_path, audit_revision=1))  # revision 1 needs nothing
    assert lfr.load_campaign(_v5(tmp_path, **{KEY: True}))  # the sandbox alone is fine


def test_the_native_sandbox_needs_unwrapped_claude_stream_drivers(tmp_path):
    for edit in ({"sandbox": True}, {"stream": {"format": "none"}}, {"sandbox": None}):
        path = _v5(tmp_path, **{KEY: True})
        data = json.loads(path.read_text("utf-8"))
        data["drivers"]["cloud_reviewer"].update(edit)
        if edit == {"sandbox": None}:
            data["drivers"]["cloud_reviewer"].pop("sandbox")
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(lfr.RunnerError, match="declare sandbox false and a claude-stream-json stream|without the claude-stream-json stream"):
            lfr.load_campaign(path)


def test_the_private_attempt_root_is_accepted_under_v5_and_still_refused_before_v4(tmp_path):
    assert lfr.load_campaign(_v5(tmp_path, private_attempt_root=True))["isolation"]["private_attempt_root"] is True
    assert lfr.load_campaign(QUALIFICATION / "pat-19-campaign-v4.json")["isolation"]["private_attempt_root"] is True
    for protocol in ("pat-19-protocol-v3", "pat-19-protocol-v4x", "other"):
        with pytest.raises(lfr.RunnerError, match="accepted only under protocol pat-19-protocol-v4"):
            lfr.load_campaign(_config(tmp_path, 2, protocol=protocol, private_attempt_root=True))
    # the other v4 keys stay v4-only: a v5 that wants them needs a loader change (not part of PAT-124)
    data = json.loads(_v5(tmp_path).read_text("utf-8"))
    data["correction_feedback"] = {"hidden_test_failures": True}
    (tmp_path / "c.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(lfr.RunnerError, match="accepted only under protocol pat-19-protocol-v4"):
        lfr.load_campaign(tmp_path / "c.json")


# ------------------------------------------------------------------------ the settings and the argv

def _zone(tmp_path, in_home=False):
    home = tmp_path / "home"
    work = (home / "work") if in_home else tmp_path / "work"
    attempt = work / "private-a" / "a"
    for d in (home / ".ssh", home / ".config" / "foundry", attempt / "bundle", attempt / "scratch",
              tmp_path / "state", tmp_path / "repo"):
        d.mkdir(parents=True, exist_ok=True)
    deny = [home / ".ssh", home / ".config" / "foundry", tmp_path / "state", tmp_path / "repo"]
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


class _Stop(Exception):
    pass


def _capture(monkeypatch, tmp_path, driver, native):
    """What ``execute_driver`` would start: argv and environment, with ``Popen`` replaced (nothing is launched)."""
    seen = {}

    def fake(argv, **kw):
        seen.update(argv=list(argv), env=dict(kw["env"]), cwd=kw["cwd"])
        raise OSError("not started: this test never launches anything")
    monkeypatch.setattr(lfr.subprocess, "Popen", fake)
    work = tmp_path / "bundle"
    work.mkdir(exist_ok=True)
    host = {"PATH": "/usr/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/fake/home", "LOGNAME": "u",
            "USER": "u", "TMPDIR": "/fake/tmp", "SECRET_TOKEN": "x", "GIT_CONFIG_GLOBAL": "/host/cfg"}
    out = lfr.execute_driver(driver, {"prompt": "P", "session_id": "S", "model": "M"}, workdir=work,
                             scratch=tmp_path / "scratch", stream_log=tmp_path / "s.jsonl", max_seconds=5,
                             max_steps=None, sandbox=False, deny_read=[], host_env=host, native_settings=native)
    assert out["start_error"]
    return seen, host


def test_without_the_key_the_v4_cloud_drivers_start_with_the_same_argv_and_environment(monkeypatch, tmp_path):
    v4 = json.loads((QUALIFICATION / "pat-19-campaign-v4.json").read_text("utf-8"))
    driver = v4["drivers"]["cloud_implementer_current"]
    (tmp_path / "a").mkdir()
    plain, host = _capture(monkeypatch, tmp_path / "a", driver, None)
    assert plain["argv"] == [a.replace("{prompt}", "P").replace("{session_id}", "S").replace("{model}", "M")
                             for a in driver["argv"]]
    assert set(plain["env"]) == {"PATH", "LANG", "LC_ALL", "HOME", "LOGNAME", "USER", "TMPDIR", "FOUNDRY_DATA"}
    (tmp_path / "b").mkdir()
    native, _ = _capture(monkeypatch, tmp_path / "b", driver, {"x": 1})
    assert set(native["env"]) - set(plain["env"]) == {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"}
    assert native["env"]["GIT_CONFIG_GLOBAL"] == os.devnull and native["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    for name in ("PATH", "LANG", "LC_ALL", "HOME", "LOGNAME", "USER", "TMPDIR"):  # the R6 allow-list is intact
        assert native["env"][name] == plain["env"][name] == host[name]
    assert "SECRET_TOKEN" not in native["env"] and native["argv"][0] == "claude"
    assert native["argv"][-4:-2] == ["--permission-mode", "dontAsk"] and json.loads(native["argv"][-1]) == {"x": 1}


# ------------------------------------------------------- the record: what the launcher verified

def _fake_runner(tmp_path, monkeypatch, *, version=HOST, native=True, rev2=True, plan=PLAN):
    """A runner whose cloud fake arms speak a Claude Code stream and record what they were given."""
    real = v2_campaign

    def campaign(tp, pl, frozen=("cand-a",), **over):
        base, plan_path = real(tp, pl, frozen, **over)
        for name in ("cloud_implementer_current", "cloud_reviewer"):
            d = base["drivers"][name]
            d.update(stream={"format": "claude-stream-json"}, sandbox=False)
            d["argv"] = [*d["argv"], "--init-tools", "Bash", *(["--init-version", version] if version else [])]
        return base, plan_path
    monkeypatch.setattr("test_local_first_exploration_runner.v2_campaign", campaign)
    iso = {"private_attempt_root": True, **({KEY: True} if native else {}), **({"audit_revision": R2} if rev2 else {})}
    (tmp_path / "x").mkdir()
    runner, _, plan_path, tasks = make_runner(tmp_path / "x", "compare_exploration", plan,
                                              campaign_over={"isolation": iso})
    runner.host_env = {**os.environ, "HOME": str(tmp_path / "home")}
    return runner, plan_path, tasks


def _native_records(plan_path):
    return [json.loads(line) for line in Path(str(plan_path) + ".native").read_text("utf-8").splitlines()]


def test_a_run_under_the_key_passes_the_settings_and_says_what_was_verified(tmp_path, monkeypatch):
    runner, plan_path, tasks = _fake_runner(tmp_path, monkeypatch)
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    record = [r for r in results(runner) if r["path"] == "A" and r["segment"] == "cloud"][0]
    assert record["audit"]["barrier"] == lfr.BARRIER_OBSERVED == "settings_transmitted_version_observed"
    assert record["audit"]["host_models"] == [f"claude-code-{HOST}"] * 2
    given = _native_records(plan_path)
    assert {g["role"] for g in given} == {"implementer", "reviewer"}
    for g in given:
        assert g["mode"] == "dontAsk" and g["git_global"] == os.devnull and g["git_nosystem"] == "1"
        s = g["settings"]
        attempt = s["permissions"]["additionalDirectories"][0]
        assert os.path.isabs(attempt) and Path(attempt).name == attempt.rsplit("/", 1)[-1]
        assert s["sandbox"]["filesystem"]["allowRead"] == [attempt] and s["sandbox"]["failIfUnavailable"] is True
        assert attempt.startswith(os.path.realpath(runner.work_root) + "/private-")
        assert "HOME" in g["env"] and "FOUNDRY_DATA" in g["env"]
    assert given[0]["settings"]["permissions"]["additionalDirectories"] != \
        given[1]["settings"]["permissions"]["additionalDirectories"]  # computed per execution


def test_without_the_key_nothing_is_passed_and_the_barrier_is_not_verified(tmp_path, monkeypatch):
    runner, plan_path, tasks = _fake_runner(tmp_path, monkeypatch, native=False)
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    assert not Path(str(plan_path) + ".native").exists()  # the fake records only when --settings is given
    record = [r for r in results(runner) if r["path"] == "A" and r["segment"] == "cloud"][0]
    assert record["audit"]["barrier"] == lfr.AUDIT_BARRIER == "not_verified"


def test_a_stream_without_a_version_only_proves_the_settings_were_transmitted(tmp_path, monkeypatch):
    runner, _, tasks = _fake_runner(tmp_path, monkeypatch, version=None)
    runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    record = [r for r in results(runner) if r["path"] == "A" and r["segment"] == "cloud"][0]
    assert record["audit"]["barrier"] == lfr.BARRIER_SETTINGS == "settings_transmitted"


def test_a_record_cut_by_an_error_carries_no_audit_so_it_is_never_readable_as_verified(tmp_path, monkeypatch):
    runner, _, tasks = _fake_runner(tmp_path, monkeypatch)
    real = runner.cloud_execution

    def execution(*args, **kw):
        real(*args, **kw)
        raise lfr.ToolsetRefused("refused after the audit", [])
    runner.cloud_execution = execution
    with pytest.raises(lfr.ToolsetRefused):
        runner.compare_exploration(tasks[:1], "cand-a", ("A",))
    cut = [r for r in results(runner) if r.get("path") == "A" and r.get("segment") == "cloud"]
    assert len(cut) == 1 and cut[0]["status"] == "tool_error" and "audit" not in cut[0]


def _stream(tmp_path, events):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _init(version=HOST, **kw):
    return {"type": "system", "subtype": "init", **({"claude_code_version": version} if version else {}), **kw}


OK = {"type": "result", "subtype": "success", "is_error": False}


@pytest.mark.parametrize("events,expected", [
    ([_init(), OK], lfr.BARRIER_OBSERVED),
    ([_init(permissionMode="dontAsk"), OK], lfr.BARRIER_OBSERVED),
    ([_init(permissionMode="bypassPermissions"), OK], lfr.AUDIT_BARRIER),  # the mode asked for was not the mode seen
    ([_init(), {**OK, "is_error": True}], lfr.BARRIER_SETTINGS),  # the host reported an error
    ([_init()], lfr.BARRIER_SETTINGS),  # cut: the session did not end
    ([_init(), _init("9.9.9"), OK], lfr.BARRIER_SETTINGS),  # two versions
    ([_init(None), OK], lfr.BARRIER_SETTINGS),
    ([OK], lfr.BARRIER_SETTINGS),
    ([], lfr.BARRIER_SETTINGS),
])
def test_the_barrier_value_follows_what_the_stream_shows(tmp_path, monkeypatch, events, expected):
    runner, _, _ = _fake_runner(tmp_path, monkeypatch)
    log = _stream(tmp_path, events)
    driver = runner.campaign["drivers"]["cloud_reviewer"]
    assert runner._stream_barrier(log, driver) == lfr.AUDIT_BARRIER  # nothing was sent for this stream yet
    runner.settings_sent.add(str(log))
    assert runner._stream_barrier(log, driver) == expected
    assert expected != "confined"
    local = runner.campaign["drivers"]["local_explorer"]
    assert runner._stream_barrier(log, local) == lfr.AUDIT_BARRIER  # a local arm is not under this barrier


def test_the_record_barrier_is_the_weakest_of_its_streams(tmp_path, monkeypatch):
    runner, _, _ = _fake_runner(tmp_path, monkeypatch)
    one, two = tmp_path / "1", tmp_path / "2"
    runner.barriers = {str(one): lfr.BARRIER_OBSERVED, str(two): lfr.BARRIER_SETTINGS}
    assert runner._audit_note([one, two])["audit"]["barrier"] == lfr.BARRIER_SETTINGS
    assert runner._audit_note([one])["audit"]["barrier"] == lfr.BARRIER_OBSERVED
    assert runner._audit_note([one, tmp_path / "missing"])["audit"]["barrier"] == lfr.AUDIT_BARRIER
    assert runner._audit_note([])["audit"]["barrier"] == lfr.AUDIT_BARRIER
    assert lfr.BARRIERS == (lfr.AUDIT_BARRIER, lfr.BARRIER_SETTINGS, lfr.BARRIER_OBSERVED)


# --------------------------------------------- PAT-123 round 4: N2 and N3 (known costs, not new behaviour)

OVERFLAGS = ["source .venv/bin/activate && pytest -q", "for i in 1 2; do break; done", "for i in 1 2; do continue; done",
             "trap 'echo bye' EXIT", 'cd "$(git rev-parse --show-toplevel)" && pytest -q',
             'git commit -q -m "cd .. then fix"']


@pytest.mark.parametrize("command", OVERFLAGS)
def test_ordinary_work_that_revision_2_over_flags_is_a_known_cost(tmp_path, command):
    """The costs listed for the v5 ticket (N3): revision 1 flags none of them; revision 2 gives up the directory,
    so the NEXT relative path of the call chain is a hit. ``break`` and ``continue`` do not move the shell, but they
    stay unknown on purpose: ending a loop early is what the audit's loop reading does not model."""
    lay = Layout(tmp_path).claude(command, "cat tests/a.py")
    old, new = lay.both()
    assert old == [] and new == [str(lfr.UNKNOWN_CWD)]


def test_the_target_of_a_dash_C_is_still_flagged_though_the_directory_of_the_program_is_not_followed(tmp_path):
    lay = Layout(tmp_path).claude("git -C ../../../ status", "make -C ../../../tests", "git -C ../../b status")
    flagged = lay.audit(R2)
    assert any(p.endswith("/work") for p in flagged) and any(p.endswith("/work/tests") for p in flagged)
    assert not any(p.endswith("/work/private-b/b") for p in flagged)  # inside the allowed zone: fine


# ------------------------------------------------------------------------------ the real trial tool

def _items(tmp_path):
    a, b, home = tmp_path / "a", tmp_path / "b", tmp_path / "home"
    for d in (a / "scratch", b / "scratch", home):
        d.mkdir(parents=True, exist_ok=True)
    return a, b, home, trial.probes(a, b, home, "n0nce")


def _call(i, name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": name,
                                                          "input": inp}]}}


def _result(i, text, error=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                                     "content": text, "is_error": error}]}}


def test_the_trial_reads_refusals_from_the_stream_and_the_disk_and_leaves_the_rest_unknown(tmp_path):
    a, b, home, items = _items(tmp_path)
    by = {p.pid: p for p in items}
    trial.prepare(items, "n0nce")
    assert by["P3"].path.read_text() == by["P3"].token and by["P11"].path.read_text() == "ORIGINAL-n0nce\n"
    denied = "Operation not permitted"
    events = [_init(), OK,
              _call(1, "Bash", command=f"cat {by['P3'].path} # PROBE-P3"), _result(1, "cat: x: " + denied, True),
              _call(2, "Read", file_path=str(by["P4"].path)), _result(2, by["P4"].token),  # a hole
              _call(3, "Bash", command="echo x > " + str(by["P9"].path) + " # PROBE-P9"), _result(3, "ok"),
              _call(4, "Write", file_path=str(by["P10"].path), content="x"), _result(4, "Permission denied", True),
              _call(5, "Edit", file_path=str(by["P11"].path)), _result(5, "boom: typo in the call", True),
              _call(6, "Bash", command="ls / # PROBE-P13"), _result(6, "Applications\nUsers\nSystem\nvar"),
              _call(7, "Bash", command="x # PROBE-P2"), _result(7, "3 tests collected"),
              _call(8, "Grep", pattern=by["P6"].token, path=str(b)), _result(8, "No matches found")]
    by["P9"].path.write_text("x")  # the shell write DID land on the disk: a hole, whatever the result text says
    lines = [json.dumps(e) for e in events]
    obs, unknown, host = trial.observe(lines, items)
    assert obs["P3"] == "refused" and obs["P4"] == "allowed" and obs["P9"] == "allowed"
    assert obs["P10"] == "refused" and obs["P13"] == "listed" and obs["P2"] == "allowed"
    assert "P11" not in obs and "typo" not in unknown["P11"] and "refusal" in unknown["P11"]  # a failure is not a refusal
    assert "P6" not in obs and "P6" in unknown  # no match is no proof of a refusal
    assert unknown["P5"] == "no such tool call in the stream" and unknown["P12"] == unknown["P5"]
    assert host == {"version": HOST, "permission_mode": None, "authenticated": "yes"}
    assert trial.root_listing(lines, by["P13"]) == ["Applications", "System", "Users", "var"]
    assert trial.observe([], items)[2]["authenticated"] == "unknown"
    assert trial.observe([json.dumps(_init()), json.dumps({**OK, "is_error": True})], items)[2]["authenticated"] == "no"
    by["P12"].path.parent.mkdir(parents=True, exist_ok=True)
    by["P12"].path.write_text("inside")
    assert trial.observe([json.dumps(_call(9, "Write", file_path=str(by["P12"].path))), json.dumps(_result(9, "ok"))],
                         items)[0]["P12"] == "allowed"


def test_the_prompt_names_every_probe_and_the_result_holds_no_personal_path(tmp_path):
    a, b, home, items = _items(tmp_path)
    text = trial.prompt(items, a, b, home, "n0nce")
    for p in items:
        assert p.needle in text or p.pid in ("P12",), p.pid
    assert "do not solve that task" in text and "single word DONE" in text
    settings = lfr.native_sandbox_settings(attempt_dir=a, work_root=tmp_path, home=home)
    out = trial.render(settings=settings, replacements=[(os.path.realpath(a), "<attempt>"), (os.path.realpath(tmp_path),
                       "<work-root>"), (os.path.realpath(home), "<home>")], observations={"P3": "refused"},
                       unknown={"P4": "no such tool call in the stream"}, host={"version": HOST, "authenticated": "yes"},
                       items=items, barrier={"implementer": lfr.BARRIER_OBSERVED}, listing=["Users"],
                       flags={"implementer": 3}, reviewer={"ran": True, "verdict_read": True}, billing={"implementer": None},
                       today=__import__("datetime").date(2026, 10, 8), campaign_name="c.json")
    body = json.loads(out)
    assert os.path.realpath(tmp_path) not in out and "<attempt>" in out and "<home>" in out
    assert body["observations"]["P3"]["as_expected"] is True and body["observations"]["P4"]["as_expected"] is None
    assert body["observations"]["P4"]["observed"] == "unknown" and body["claude_code_version"] == HOST
    assert body["billing_total"] == {"implementer": None} and "session" not in out.lower().replace("session_authenticated", "")
    with pytest.raises(lfr.RunnerError, match="personal path"):
        trial.render(settings={"x": "/Users/someone/y"}, replacements=[], observations={}, unknown={}, host={},
                     items=[], barrier={}, listing=None, flags={}, reviewer={}, billing={},
                     today=__import__("datetime").date(2026, 10, 8), campaign_name="c.json")


def test_the_verb_refuses_before_any_spend(tmp_path, capsys):
    out = tmp_path / "r.json"
    base = ["native-sandbox-trial", "--campaign", str(QUALIFICATION / "pat-19-campaign-v4.json"),
            "--state-dir", str(tmp_path / "s"), "--work-root", str(tmp_path / "w"), "--snapshot",
            str(QUALIFICATION / "pat-19-corpus-snapshot-v1.json"), "--task-pr", "1", "--out", str(out)]
    assert lfr.main([*base, "--envelope", str(tmp_path / "none.json")]) == 2  # no envelope
    assert "no authorization envelope" in capsys.readouterr().err
    out.write_text("{}")
    assert lfr.main([*base, "--envelope", str(tmp_path / "none.json")]) == 2  # never rewrites a result
    assert "never rewritten" in capsys.readouterr().err
    out.unlink()
    assert not (tmp_path / "w").exists() and not (tmp_path / "s").exists()


def test_the_trial_end_to_end_with_fake_arms_writes_a_result_without_spending_anything_real(tmp_path, monkeypatch):
    """The whole verb with fake arms: nothing is launched but the fake scripts, the result names what the stream
    did not show as unknown, holds no path of the home and ends with the temporary files removed."""
    monkeypatch.setattr(lfr, "sandbox_available", lambda: True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    real = v2_campaign

    def campaign(tp, pl, frozen=("cand-a",), **over):
        base, plan_path = real(tp, pl, frozen, **over)
        for name in ("cloud_implementer_current", "cloud_reviewer"):
            d = base["drivers"][name]
            d.pop("fake")
            d.update(stream={"format": "claude-stream-json"}, sandbox=False, verified=True)
            d["argv"] = [*d["argv"], "--init-tools", "Bash", "--init-version", HOST]
        return base, plan_path
    inputs = tmp_path / "in"  # apart from the work root (the launcher refuses a profile that denies its parent)
    inputs.mkdir()
    base, plan_path = campaign(inputs, PLAN)
    base["prompts"]["native_trial"] = "placeholder"
    repo, snap, _, _ = _make_repo(inputs)
    snapshot = inputs / "snapshot.json"
    snapshot.write_text(json.dumps(snap), encoding="utf-8")
    envelope = _envelope(inputs, ["compare_exploration"], "trial-1")
    args = argparse.Namespace(campaign=str(QUALIFICATION / "pat-19-campaign-v2.json"), envelope=str(envelope),
                              state_dir=str(tmp_path / "state"), work_root=str(tmp_path / "work"), repo=str(repo),
                              snapshot=str(snapshot), task_pr=snap["prs"][0]["pr"], no_reviewer=False,
                              out=str(tmp_path / "result.json"))
    import datetime as dt
    assert trial.run(args, base, today=dt.date(2026, 10, 6)) == 0
    body = json.loads((tmp_path / "result.json").read_text("utf-8"))
    assert body["schema"] == trial.TRIAL_SCHEMA and body["claude_code_version"] == HOST
    assert body["observations"]["session_authenticated"] == "yes"
    assert body["barrier_in_records"] == {"implementer": lfr.BARRIER_OBSERVED, "reviewer": lfr.BARRIER_OBSERVED}
    assert body["reviewer"]["ran"] is True and body["reviewer"]["verdict_read"] is True
    assert body["observations"]["P3"]["observed"] == "unknown" and body["unknown"]["P3"]
    assert body["permission_mode_asked"] == "dontAsk"
    text = (tmp_path / "result.json").read_text("utf-8")
    assert str(tmp_path) not in text and str(home) not in text
    assert list(home.iterdir()) == []  # the home sentinels are removed
    assert not list((tmp_path / "work").glob("**/bundle"))  # both bundles are discarded
