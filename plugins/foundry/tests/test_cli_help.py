"""PAT-286: `-h` / `--help` on every launcher module prints usage and reaches no tracker.

The environment names a tracker that does not exist and an empty home: a module that
built an adapter, wrote a registry entry or created a file would fail or leave a trace.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "tooling" / "foundry_cli.py"

# (launcher module, arguments before the help flag): the help flag sits in the position
# where a positional value used to be written (title, issue id, manifest path, ...).
CASES = [
    ("adr", ["create"]), ("adr", ["accept"]), ("adr", ["edit", "ADR-1"]),
    ("adr", ["supersede", "ADR-1"]), ("adr", ["link-issue", "ADR-1"]), ("adr", []),
    ("issue", ["start"]), ("issue", ["openpr"]), ("issue", ["merge", "X-1"]),
    ("issue", ["close-epic"]), ("issue", ["accept"]), ("issue", ["wait-ci"]), ("issue", []),
    ("edit", ["create-issue"]), ("edit", ["transition", "X-1"]), ("edit", []),
    ("frame", []), ("query", []), ("query", ["issue"]),
    ("setup_project", []), ("setup_project", ["Name", "KEY"]),
    ("cutover", []), ("cutover", ["preflight", "linear", "cfg.json"]),
    ("registry", []), ("registry", ["register", "linear"]), ("registry", ["alias", "t", "s"]),
    ("doctor", []), ("configure", []), ("configure", ["set"]), ("routing", []),
    ("routing", ["prepare-review"]), ("telemetry", []), ("local-scout", []),
    ("local-code", []), ("devhub-smoke", []), ("epic-preview", []), ("command-worker", []),
    ("campaign-gate", []), ("campaign-retry", []), ("campaign-reconcile", []),
    ("campaign-review-remediation", []), ("campaign-legacy", []), ("campaign-terminal", []),
    ("cost-attribution", []), ("delivery-audit", []), ("campaign-approval", []),
    ("campaign-cadence", []),
]


def test_every_launcher_module_is_covered():
    text = CLI.read_text()
    block = text[text.index("_MODULES = ("):]
    names = set(re.findall(r'"([a-z][a-z_-]*)"', block[:block.index(")")]))
    assert len(names) >= 25 and "adr" in names
    assert names - {name for name, _ in CASES} == set()


@pytest.mark.parametrize("flag", ["--help", "-h"])
@pytest.mark.parametrize("module, before", CASES, ids=lambda v: "-".join(v) if isinstance(v, list) else v)
def test_help_prints_usage_and_writes_nothing(tmp_path, module, before, flag):
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "XDG_CONFIG_HOME": str(home),
           "FOUNDRY_DATA": str(home / "data"), "FOUNDRY_TRACKER": "none"}
    done = subprocess.run(
        [sys.executable, str(CLI), module, *before, flag], cwd=cwd, env=env,
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, check=False,
    )
    assert done.returncode == 0, (done.stdout, done.stderr)
    shown = done.stdout + done.stderr
    assert ("usage" in shown.lower() or "python3 -m foundry." in shown) and "Traceback" not in shown
    assert list(cwd.iterdir()) == [] and list(home.iterdir()) == []
