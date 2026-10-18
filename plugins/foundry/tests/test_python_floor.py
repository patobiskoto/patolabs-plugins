"""PAT-265: the declared minimum Python, checked before any foundry import.

No interpreter below the minimum exists locally or in CI, so an unsupported version is
simulated by replacing ``sys.version_info`` in a child process that then runs the real
hook or launcher file. The routing test runs under whichever interpreter runs the suite,
and the dedicated CI job runs it under the declared minimum.
"""
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
_RUNNER = (
    "import runpy, sys; sys.version_info = (3, 9, 6, 'final', 0); "
    "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
)


def _run_old(script, *args, stdin="{}", home):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "FOUNDRY_DATA": str(home / "data")}
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
    assert recorded == {"executable": sys.executable, "version": [3, 9, 6]}


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
        json.dumps({"executable": executable, "version": list(version)}), encoding="utf-8")


def test_doctor_reads_the_interpreter_observed_by_the_hook():
    _record((3, 12, 1))
    result = doctor.hook_python_diagnostic()
    assert result["ok"] is True
    assert "/opt/hook/python3" in result["detail"] and "3.12.1" in result["detail"]
    assert "observed by hook" in result["detail"] and "3.11" in result["detail"]


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
