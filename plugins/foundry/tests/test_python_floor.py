"""PAT-265: the declared minimum Python, checked before any foundry import.

No interpreter below the minimum exists locally or in CI, so an unsupported version is
simulated by replacing ``sys.version_info`` in a child process that then runs the real
hook or launcher file. The routing test runs under whichever interpreter runs the suite,
and the dedicated CI job runs it under the declared minimum.
"""
import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import python_floor
from foundry import doctor, registry

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN_ROOT / "hooks"
OLD = (3, 9, 6, "final", 0)

# Runs a file as __main__ with sys.version_info replaced by a 3.9 tuple.
# PAT-267: the fixed absolute paths of the search list are emptied so that a real
# interpreter of the machine is never found; candidates come from the child PATH only.
_RUNNER = (
    "import runpy, sys; sys.version_info = (3, 9, 6, 'final', 0); "
    "sys.path.insert(0, %r); import python_floor; python_floor.FIXED_PATHS = (); "
    "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
) % str(Path(__file__).resolve().parents[1] / "tooling")


def _run_old(script, *args, stdin="{}", home, path=None, extra_env=None):
    empty = home / "empty-bin"
    empty.mkdir(exist_ok=True)
    env = {"PATH": str(path or empty), "HOME": str(home), "FOUNDRY_DATA": str(home / "data"),
           **(extra_env or {})}
    return subprocess.run(
        [sys.executable, "-c", _RUNNER, str(script), *args],
        input=stdin, env=env, capture_output=True, text=True,
    )


def _load_route_agent():
    spec = importlib.util.spec_from_file_location("route_agent_pat265", HOOKS / "route_agent.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["route_agent_pat265"] = module
    spec.loader.exec_module(module)
    return module


def test_minimum_is_declared_once():
    assert python_floor.MINIMUM_PYTHON == (3, 11)
    assert python_floor.data_dir() == registry.data_dir()


def test_message_names_interpreter_version_and_minimum():
    message = python_floor.unsupported_message(OLD, "/opt/old/python3")
    assert "/opt/old/python3" in message and "3.9.6" in message and "3.11" in message
    assert python_floor.unsupported_message((3, 11, 0, "final", 0)) is None
    assert python_floor.unsupported_message(sys.version_info) is None


@pytest.mark.parametrize("hook", [
    "route_agent", "guard_bash", "observe_agent", "session_start",
])
def test_hook_under_unsupported_python_is_visible_and_never_denies(hook, tmp_path):
    payload = {"tool_input": {"command": "gh pr create", "subagent_type": "foundry:implementer"},
               "cwd": str(tmp_path)}
    result = _run_old(HOOKS / f"{hook}.py", stdin=json.dumps(payload), home=tmp_path)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert set(output) == {"systemMessage"}
    assert "3.9.6" in output["systemMessage"] and "3.11" in output["systemMessage"]
    assert sys.executable in output["systemMessage"]


def test_session_start_records_the_interpreter_even_when_unsupported(tmp_path):
    _run_old(HOOKS / "session_start.py", home=tmp_path)
    recorded = json.loads((tmp_path / "data" / python_floor.OBSERVED_FILE).read_text())
    assert recorded.pop("recorded_at").endswith("Z")
    assert recorded == {"executable": sys.executable, "version": [3, 9, 6]}


def test_cli_reexecutes_when_a_candidate_exists(tmp_path):
    bin_dir = tmp_path / "bin"
    log = _fake(bin_dir, "python3.12")
    result = _run_old(PLUGIN_ROOT / "tooling" / "foundry_cli.py", "doctor",
                      home=tmp_path, path=bin_dir)
    assert result.returncode == 0
    assert log.read_text().splitlines()[0] == (
        f"ARGS:{PLUGIN_ROOT / 'tooling' / 'foundry_cli.py'} doctor")


def test_cli_refuses_under_unsupported_python(tmp_path):
    result = _run_old(PLUGIN_ROOT / "tooling" / "foundry_cli.py", "doctor", home=tmp_path)
    assert result.returncode != 0
    assert sys.executable in result.stderr and "3.9.6" in result.stderr and "3.11" in result.stderr
    assert "Traceback" not in result.stderr


def test_hook_failure_diagnostic_names_the_type_only():
    text = json.loads(python_floor.hook_failure_output(
        "route_agent", TypeError("secret-token /private/path")))["systemMessage"]
    assert "TypeError" in text and "secret-token" not in text and "/private" not in text


def test_route_agent_internal_error_fails_open_but_visible(tmp_path, monkeypatch, capsys):
    import io
    route_agent = _load_route_agent()
    monkeypatch.setattr(route_agent, "route_tool_input",
                        lambda *a, **k: (_ for _ in ()).throw(TypeError("boom")))
    payload = {"tool_input": {"subagent_type": "foundry:implementer", "prompt": "x"},
               "cwd": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    route_agent.main()
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"systemMessage"} and "TypeError" in output["systemMessage"]
    assert "boom" not in output["systemMessage"]


def test_issue_route_request_without_escalation_state_routes(tmp_path):
    route_agent = _load_route_agent()
    packet = ("Goal:\nx\nInputs:\ny\nConstraints:\nz\nDone when:\nw")
    updated, _context = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer",
         "prompt": 'FOUNDRY_ROUTE_REQUEST={"issue":"PAT-265"}\n' + packet},
        cwd=tmp_path, environ={},
    )
    assert updated["subagent_type"].startswith("foundry:routed-worker-")


# -- doctor ----------------------------------------------------------------

def _record(version, executable="/opt/hook/python3"):
    directory = Path(python_floor.data_dir())
    directory.mkdir(parents=True, exist_ok=True)
    (directory / python_floor.OBSERVED_FILE).write_text(
        json.dumps({"executable": executable, "version": list(version),
                    "recorded_at": "2026-10-18T10:00:00Z"}), encoding="utf-8")


def test_doctor_reads_the_interpreter_observed_by_the_hook():
    _record((3, 12, 1))
    result = doctor.hook_python_diagnostic()
    assert result["ok"] is True
    assert "/opt/hook/python3" in result["detail"] and "3.12.1" in result["detail"]
    assert "observed by hook at 2026-10-18T10:00:00Z" in result["detail"] and "3.11" in result["detail"]


def test_doctor_fails_below_the_minimum():
    _record((3, 9, 6))
    result = doctor.hook_python_diagnostic()
    assert result["ok"] is False and "3.9.6" in result["detail"]


def test_doctor_falls_back_to_python3_on_its_own_path():
    answer = subprocess.CompletedProcess([], 0, stdout="3.9.6\n")
    result = doctor.hook_python_diagnostic(
        runner=lambda *a, **k: answer, which=lambda name: "/usr/bin/python3")
    assert result["ok"] is False
    assert "resolved on doctor PATH" in result["detail"] and "/usr/bin/python3" in result["detail"]


def test_doctor_reports_a_missing_python3():
    result = doctor.hook_python_diagnostic(which=lambda name: None)
    assert result["ok"] is False and "not found" in result["detail"]


def test_doctor_prints_hook_python_before_the_config_preflight(monkeypatch, capsys):
    def refuse():
        raise SystemExit("config broken")
    monkeypatch.setattr(doctor, "_provider_transport_preflight", refuse)
    doctor.main([])
    out = capsys.readouterr().out
    assert out.index("Hook Python") < out.index("Config")


@pytest.mark.parametrize("stdin", ["", "not json", "null", "[]", '"text"'])
def test_route_agent_unreadable_or_non_object_payload_is_visible_and_fail_open(
        stdin, monkeypatch, capsys):
    import io
    route_agent = _load_route_agent()
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    route_agent.main()
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"systemMessage"} and "route_agent" in output["systemMessage"]


def test_route_agent_out_of_scope_call_stays_silent(monkeypatch, capsys):
    import io
    route_agent = _load_route_agent()
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"tool_input": {"subagent_type": "other"}}'))
    route_agent.main()
    assert capsys.readouterr().out == ""


def test_startup_files_parse_as_python_39():
    """`feature_version=(3, 9)` rejects e.g. `match` and `except*` (checked by the first
    two assertions) but NOT parenthesized context managers nor runtime `X | Y`
    annotations; for the latter the hooks must keep `from __future__ import annotations`
    (asserted below). The real 3.9.6 run is a manual step of the PR."""
    for source in ("match x:\n case 1: pass\n", "try:\n pass\nexcept* E:\n pass\n"):
        with pytest.raises(SyntaxError):
            ast.parse(source, feature_version=(3, 9))
    files = [PLUGIN_ROOT / "tooling" / "python_floor.py"] + [
        HOOKS / f"{name}.py" for name in (
            "route_agent", "guard_bash", "observe_agent", "session_start",
            "guard_routed_readonly")] + [PLUGIN_ROOT / "tooling" / "foundry_cli.py"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        ast.parse(text, feature_version=(3, 9))
        if path.name not in ("session_start.py", "foundry_cli.py"):
            assert "from __future__ import annotations" in text, path.name


# -- PAT-267: re-execution under a supported interpreter ---------------------

def _fake(directory, name, reported="3 12", body=None):
    """Executable that answers the version probe and otherwise logs argv and stdin."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    log = directory / f"{name}.log"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ]; then echo "%s"; exit 0; fi\n'
        'echo "ARGS:$*" >> "%s"\n'
        'echo "ENV:$FOUNDRY_PYTHON_REEXEC|$FOUNDRY_PYTHON_REEXEC_FROM" >> "%s"\n'
        'echo "STDIN:$(/bin/cat)" >> "%s"\n%s'
        % (reported, log, log, log, body or "echo '{\"ok\": 1}'\n"))
    script.chmod(0o755)
    return log


def test_reexec_runs_the_same_script_with_arguments_and_stdin_intact(tmp_path):
    bin_dir = tmp_path / "bin"
    log = _fake(bin_dir, "python3.12")
    result = _run_old(HOOKS / "guard_bash.py", "extra", stdin='{"payload": 1}',
                      home=tmp_path, path=bin_dir)
    assert result.returncode == 0 and json.loads(result.stdout) == {"ok": 1}
    lines = log.read_text().splitlines()
    assert lines[0] == f"ARGS:{HOOKS / 'guard_bash.py'} extra"
    assert lines[1].startswith("ENV:1|3.9.6 ") and sys.executable in lines[1]
    assert lines[2] == 'STDIN:{"payload": 1}'


def test_reexec_order_prefers_newer_python3n_then_python3(tmp_path):
    bin_dir = tmp_path / "bin"
    older = _fake(bin_dir, "python3.11")
    newer = _fake(bin_dir, "python3.13")
    plain = _fake(bin_dir, "python3")
    _run_old(HOOKS / "observe_agent.py", home=tmp_path, path=bin_dir)
    assert newer.exists() and not older.exists() and not plain.exists()


def test_reexec_skips_candidates_that_are_too_old_or_broken(tmp_path):
    bin_dir = tmp_path / "bin"
    too_old = _fake(bin_dir, "python3.13", reported="3 10")
    broken = _fake(bin_dir, "python3.12", body="")
    (bin_dir / "python3.12").write_text("#!/bin/sh\nexit 3\n")
    good = _fake(bin_dir, "python3")
    result = _run_old(HOOKS / "guard_bash.py", home=tmp_path, path=bin_dir)
    assert result.returncode == 0 and good.exists()
    assert not too_old.exists() and not broken.exists()


def test_no_candidate_keeps_the_2_1_0_message(tmp_path):
    bin_dir = tmp_path / "bin"
    _fake(bin_dir, "python3.12", reported="3 9")
    result = _run_old(HOOKS / "guard_bash.py", home=tmp_path, path=bin_dir)
    output = json.loads(result.stdout)
    assert result.returncode == 0 and set(output) == {"systemMessage"}
    assert "3.9.6" in output["systemMessage"]


def test_guard_variable_prevents_a_second_reexec(tmp_path):
    bin_dir = tmp_path / "bin"
    log = _fake(bin_dir, "python3.12")
    result = _run_old(HOOKS / "guard_bash.py", home=tmp_path, path=bin_dir,
                      extra_env={python_floor.REEXEC_GUARD: "1"})
    assert not log.exists()
    assert set(json.loads(result.stdout)) == {"systemMessage"}


def test_execv_failure_falls_back_and_cleans_the_environment(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    _fake(bin_dir, "python3.12")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(python_floor, "FIXED_PATHS", ())
    monkeypatch.setattr(python_floor, "_attempted", False)
    monkeypatch.delenv(python_floor.REEXEC_GUARD, raising=False)
    monkeypatch.setattr(python_floor.sys, "version_info", OLD)
    monkeypatch.setattr(python_floor.os, "execv",
                        lambda *a: (_ for _ in ()).throw(OSError("denied")))
    python_floor.reexec_if_unsupported(["x.py"])
    assert python_floor.REEXEC_GUARD not in os.environ
    assert python_floor.REEXEC_FROM not in os.environ
    assert "3.9.6" in python_floor.hook_unsupported_output("h")


def test_supported_interpreter_never_searches(monkeypatch):
    monkeypatch.setattr(python_floor, "_attempted", False)
    monkeypatch.setattr(python_floor, "_candidates",
                        lambda: (_ for _ in ()).throw(AssertionError("searched")))
    python_floor.reexec_if_unsupported(["x.py"])


def test_candidates_are_the_closed_list_in_order(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    local = tmp_path / "home" / ".local" / "bin"
    for directory, name in ((bin_dir, "python3.11"), (bin_dir, "python3.13"),
                            (bin_dir, "python3"), (local, "python3.12"),
                            (bin_dir, "python3.99"), (bin_dir, "python3.10")):
        _fake(directory, name)
    fixed = _fake(tmp_path / "fixed", "python3")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(python_floor, "FIXED_PATHS", (str(tmp_path / "fixed" / "python3"),))
    names = [os.path.relpath(p, tmp_path) for p in python_floor._candidates()]
    assert fixed.parent.name == "fixed" and fixed.name == "python3.log"
    assert names == [
        "bin/python3.13", "bin/python3.11", "bin/python3", "fixed/python3",
        "home/.local/bin/python3.12"]


def test_session_start_records_the_reexecuting_interpreter_only_after_exec(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    data = tmp_path / "data"
    # The "supported" interpreter is the real one, wrapped so the hook runs for real; the
    # wrapper first logs whether a record already exists (the old process must write none)
    # and what the child environment holds.
    wrapper = bin_dir / "python3.12"
    wrapper.write_text(
        '#!/bin/sh\nif [ "$1" = "-c" ]; then echo "3 12"; exit 0; fi\n'
        'if [ -e "%s/%s" ]; then echo present > "%s/before"; else echo absent > "%s/before"; fi\n'
        'exec "%s" -c "import os, sys, runpy; sys.argv = sys.argv[1:]; '
        'runpy.run_path(sys.argv[0], run_name=\'__main__\')" "$@" \n'
        % (data, python_floor.OBSERVED_FILE, tmp_path, tmp_path, sys.executable))
    wrapper.chmod(0o755)
    data.mkdir()
    result = _run_old(HOOKS / "session_start.py", home=tmp_path, path=bin_dir, stdin="{}")
    assert result.returncode == 0
    assert (tmp_path / "before").read_text().strip() == "absent"
    recorded = json.loads((data / python_floor.OBSERVED_FILE).read_text())
    assert recorded["executable"] == sys.executable
    assert recorded["version"] == list(tuple(sys.version_info)[:3])
    assert recorded["reexec_from"] == {"executable": sys.executable, "version": [3, 9, 6]}


def test_reexecuted_process_drops_the_variables_but_keeps_the_origin(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "env.txt"
    wrapper = bin_dir / "python3.12"
    wrapper.write_text(
        '#!/bin/sh\nif [ "$1" = "-c" ]; then echo "3 12"; exit 0; fi\n'
        'exec "%s" -c "import os, sys, runpy; sys.argv = sys.argv[1:]; '
        'sys.path.insert(0, \'%s\'); import python_floor; '
        'python_floor.reexec_if_unsupported(); '
        'open(\'%s\', \'w\').write(repr((os.environ.get(\'FOUNDRY_PYTHON_REEXEC\'), '
        'os.environ.get(\'FOUNDRY_PYTHON_REEXEC_FROM\'), python_floor._origin)))" "$@"\n'
        % (sys.executable, PLUGIN_ROOT / "tooling", out))
    wrapper.chmod(0o755)
    _run_old(HOOKS / "session_start.py", home=tmp_path, path=bin_dir)
    guard, origin_var, origin = eval(out.read_text())
    assert guard is None and origin_var is None
    assert origin == {"executable": sys.executable, "version": (3, 9, 6)}


def test_unexpected_error_in_the_search_falls_back_visibly(monkeypatch):
    monkeypatch.setattr(python_floor, "_attempted", False)
    monkeypatch.setattr(python_floor.sys, "version_info", OLD)
    monkeypatch.setattr(python_floor, "_candidates",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    python_floor.reexec_if_unsupported(["x.py"])
    assert "3.9.6" in python_floor.hook_unsupported_output("h")


def test_read_observed_accepts_records_without_and_with_reexec_from():
    _record((3, 12, 1))
    assert python_floor.read_observed()["reexec_from"] is None
    path = Path(python_floor.data_dir()) / python_floor.OBSERVED_FILE
    data = json.loads(path.read_text())
    data["reexec_from"] = {"executable": "/old/python3", "version": [3, 9, 6]}
    path.write_text(json.dumps(data))
    assert python_floor.read_observed()["reexec_from"] == {
        "executable": "/old/python3", "version": (3, 9, 6)}
    data["reexec_from"] = {"executable": 1, "version": "x"}
    path.write_text(json.dumps(data))
    assert python_floor.read_observed()["reexec_from"] is None


def test_doctor_shows_where_the_hook_was_re_executed_from():
    _record((3, 12, 1))
    assert "re-executed" not in doctor.hook_python_diagnostic()["detail"]
    path = Path(python_floor.data_dir()) / python_floor.OBSERVED_FILE
    data = json.loads(path.read_text())
    data["reexec_from"] = {"executable": "/old/python3", "version": [3, 9, 6]}
    path.write_text(json.dumps(data))
    result = doctor.hook_python_diagnostic()
    assert result["ok"] is True
    assert "re-executed from /old/python3 Python 3.9.6" in result["detail"]
