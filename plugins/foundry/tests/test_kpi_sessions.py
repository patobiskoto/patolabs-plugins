"""PLP-342: discovery of the host session logs of an issue, on fake logs under tmp_path only."""
import json
import re
import subprocess
import sys
from pathlib import Path

from foundry import invocation_log, kpi_sessions

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO = "synthetic-repository"
ROOT = Path("/synthetic/checkout")
TOOL_USE = "toolu_SYNTHETIC1"
EXEC_ID = "0123456789abcdef"


def _seed(state, issue, ident, host):
    invocation_log._append(invocation_log._path(REPO, state), {
        "event": "routed", "id": ident, "issue": issue, "host": host, "role": "implementer",
        "at": "2026-10-01T00:00:00.000000Z", "status": "routed"})


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _claude(projects, root, session, tool_use):
    main = _write(projects / kpi_sessions.claude_slug(root) / f"{session}.jsonl", [{"type": "user"}])
    sub = main.parent / session / "subagents" / "agent-a.jsonl"
    _write(sub, [{"type": "user"}])
    sub.with_name("agent-a.meta.json").write_text(json.dumps({"toolUseId": tool_use}))
    return main


def _codex(sessions, name, cwd, execution_id):
    return _write(sessions / "2026" / "10" / "01" / f"rollout-{name}.jsonl", [{
        "type": "session_meta",
        "payload": {"id": name, "cwd": str(cwd), "parent_thread_id": "p",
                    "agent_path": f"root/foundry_implementer_{execution_id}"}}])


def _find(tmp_path, issues=("PLP-1",), roots=(ROOT,)):
    return kpi_sessions.discover(list(issues), REPO, roots=roots, claude_projects=tmp_path / "projects",
                                 codex_sessions=tmp_path / "codex", state_dir=tmp_path / "state")


def test_finds_the_logs_that_hold_an_invocation_of_the_issue_in_this_repository_only(tmp_path):
    state = tmp_path / "state"
    _seed(state, "PLP-1", invocation_log.claude_invocation_id(TOOL_USE), "claude")
    _seed(state, "PLP-1", EXEC_ID, "codex")
    _seed(state, "PLP-2", invocation_log.claude_invocation_id("toolu_OTHER"), "claude")
    main = _claude(tmp_path / "projects", ROOT, "s-1", TOOL_USE)
    _claude(tmp_path / "projects", ROOT, "s-2", "toolu_OTHER")             # another issue
    _claude(tmp_path / "projects", Path("/synthetic/elsewhere"), "s-3", TOOL_USE)  # another repository
    rollout = _codex(tmp_path / "codex", "r1", ROOT / "sub", EXEC_ID)      # a subdirectory of the checkout
    _codex(tmp_path / "codex", "r2", "/synthetic/elsewhere", EXEC_ID)      # another repository
    assert _find(tmp_path) == ([str(main)], [str(rollout)])
    assert _find(tmp_path, ("PLP-3",)) == ([], [])


def test_an_id_found_in_two_logs_is_ambiguous_and_nothing_is_found_leaves_nothing(tmp_path):
    state = tmp_path / "state"
    _seed(state, "PLP-1", invocation_log.claude_invocation_id(TOOL_USE), "claude")
    _seed(state, "PLP-1", EXEC_ID, "codex")
    assert _find(tmp_path) == ([], [])                                      # no log at all
    _claude(tmp_path / "projects", ROOT, "s-1", TOOL_USE)
    _claude(tmp_path / "projects", ROOT, "s-2", TOOL_USE)
    _codex(tmp_path / "codex", "r1", ROOT, EXEC_ID)
    _codex(tmp_path / "codex", "r2", ROOT, EXEC_ID)
    assert _find(tmp_path) == ([], [])


def test_a_log_holding_an_ambiguous_id_is_not_selected_for_its_other_ids(tmp_path):
    state = tmp_path / "state"
    for tool in ("toolu_X", TOOL_USE, "toolu_Z"):
        _seed(state, "PLP-1", invocation_log.claude_invocation_id(tool), "claude")
    only_y = _claude(tmp_path / "projects", ROOT, "s-0", "toolu_X")        # X is unique to this log
    _claude(tmp_path / "projects", ROOT, "s-1", TOOL_USE)                   # Y is in this log and
    mixed = _claude(tmp_path / "projects", ROOT, "s-2", TOOL_USE)           # in this one, with Z
    extra = mixed.parent / "s-2" / "subagents" / "agent-b.jsonl"
    _write(extra, [{"type": "user"}])
    extra.with_name("agent-b.meta.json").write_text(json.dumps({"toolUseId": "toolu_Z"}))
    assert _find(tmp_path) == ([str(only_y)], [])


def test_slug_matches_the_directory_names_of_the_host():
    assert kpi_sessions.claude_slug(Path("/Users/x/.codex/worktrees/a_b")) == "-Users-x--codex-worktrees-a-b"


def test_cli_without_session_uses_the_discovery_and_tokens_stay_unavailable_when_nothing_is_found(tmp_path):
    state, home = tmp_path / "state", tmp_path / "home"
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    ident = invocation_log.claude_invocation_id(TOOL_USE)
    repository = str(root.resolve())  # no origin: the identity is the root path
    invocation_log._append(invocation_log._path(repository, state), {
        "event": "routed", "id": ident, "issue": "PLP-1", "host": "claude", "role": "implementer",
        "model_requested": "sonnet-5.5", "at": "2026-10-01T00:00:00.000000Z", "status": "routed"})
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(home), "FOUNDRY_DATA": str(state),
           "PYTHONPATH": str(PLUGIN_ROOT / "tooling")}
    out = tmp_path / "export.json"
    cmd = [sys.executable, "-m", "foundry.routing", "kpi-export", "PLP-1", "--output", str(out), "--root", str(root)]
    assert subprocess.run(cmd, capture_output=True, text=True, env=env).returncode == 0
    attempt = json.loads(out.read_text())["batches"][0]["records"][0]
    assert attempt["tokens"]["input"] is None
    main = _claude(home / ".claude" / "projects", root.resolve(), "s-1", TOOL_USE)
    assert main.exists()
    done = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert str(home) not in done.stdout + done.stderr and str(root) not in done.stdout + done.stderr
    # the discovered log holds no request: still unavailable, never guessed
    assert json.loads(out.read_text())["batches"][0]["records"][0]["tokens"]["input"] is None


def test_only_the_cli_verb_and_the_merge_step_import_the_discovery():
    importers = {p.name for p in (PLUGIN_ROOT / "tooling").rglob("*.py")
                 if re.search(r"^\s*(from foundry import .*\bkpi_sessions\b|from foundry\.kpi_sessions\b)",
                              p.read_text(), re.M)}
    assert importers == {"routing.py", "kpi_merge.py"}  # PLP-343
