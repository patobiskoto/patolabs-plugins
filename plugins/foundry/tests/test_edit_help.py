"""PAT-275: `edit <command> --help` prints usage and never reaches the tracker."""
import subprocess
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "tooling" / "foundry_cli.py"


def _run(*args):
    return subprocess.run([sys.executable, str(CLI), "edit", *args], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "FOUNDRY_TRACKER": "none"}, check=False)


def test_create_issue_help_prints_usage():
    done = _run("create-issue", "--help")
    assert done.returncode == 0, done.stderr
    assert "create-issue" in done.stdout and "Traceback" not in done.stderr


def test_global_help_lists_commands():
    done = _run("--help")
    assert done.returncode == 0 and "reconcile-create" in done.stdout


def test_unknown_command_is_a_usage_error_not_a_traceback():
    done = _run("nope")
    assert done.returncode == 2 and "Traceback" not in done.stderr
