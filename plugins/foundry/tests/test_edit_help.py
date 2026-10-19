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


def test_unknown_command_usage_goes_to_stderr_not_stdout():
    """PAT-286: the usage of an error is not data on standard output."""
    done = _run("nope")
    assert done.stdout == ""
    assert "Commande inconnue" in done.stderr and "reconcile-create" in done.stderr
    assert _run().stdout == "" and _run().returncode == 2


def test_global_help_stays_on_stdout_with_exit_0():
    done = _run("-h")
    assert done.returncode == 0 and done.stderr == "" and "foundry.edit transition" in done.stdout


def test_transition_help_keeps_the_continuation_lines_of_its_entry():
    """PAT-286: `dropped` / `backlog` (PAT-ADR-0017, PAT-165) are continuation lines."""
    done = _run("transition", "--help")
    assert done.returncode == 0
    assert "dropped" in done.stdout and "backlog" in done.stdout
    assert "foundry.edit link" not in done.stdout and "foundry.edit set-field" not in done.stdout


def test_a_single_line_entry_is_only_its_own_line():
    done = _run("link", "-h")
    assert done.stdout.strip() == "python3 -m foundry.edit link <SRC-ID> <link-type> <DST-ID>"
    last = _run("body", "-h")
    assert last.stdout.strip().count("\n") == 0 and "foundry.edit body" in last.stdout


def test_the_cli_docstring_documents_help_and_the_unknown_command_exit_code():
    from foundry import edit
    doc = " ".join((edit.__doc__ or "").split())
    assert "exactly equal to `-h` or `--help` after a command prints that command's usage" in doc
    assert "writes nothing" in doc and "unknown command prints" in doc and "exits 2" in doc
