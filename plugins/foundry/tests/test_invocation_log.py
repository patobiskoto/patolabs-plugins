"""PLP-316: local record of routed implementer/reviewer invocations. Offline."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


from foundry import invocation_log
from foundry.routing_facades import AGENT_IDENTITIES, codex_spawn_plan

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BASE = "b" * 40


def _load_hook(name):
    spec = importlib.util.spec_from_file_location(name, PLUGIN_ROOT / "hooks" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


route_agent = _load_hook("route_agent")
observe_agent = _load_hook("observe_agent")


def _packet():
    return ("Goal:\nx\nInputs:\ny\nConstraints:\nz\nDone when:\nw")


def _repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "T"],
                ["git", "remote", "add", "origin", "https://example.invalid/x.git"]):
        subprocess.run(cmd, cwd=root, check=True)
    return _commit(root, "a")


def _commit(root: Path, text: str) -> str:
    (root / "f.txt").write_text(text)
    subprocess.run(["git", "add", "f.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", text], cwd=root, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def _lines(root):
    path = invocation_log._path(invocation_log._repository(root), None)
    return [json.loads(line) for line in path.read_text().splitlines()], path


def _claude(root, role, issue, tool_use_id):
    out = route_agent.route_tool_input(
        {"subagent_type": f"foundry:{AGENT_IDENTITIES[role]}",
         "prompt": f'FOUNDRY_ROUTE_REQUEST={{"issue":"{issue}"}}\n' + _packet()},
        cwd=root, environ={}, correlation=tool_use_id,
    )
    assert out is not None


def test_claude_routed_record_has_only_closed_fields_and_status_update(tmp_path):
    head = _repo(tmp_path)
    _claude(tmp_path, "implementer", "PLP-5", "toolu_secret_1")
    _claude(tmp_path, "reviewer", "PLP-5", "toolu_secret_2")
    _claude(tmp_path, "implementer", None or "PLP-6", "toolu_3")
    events, path = _lines(tmp_path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert all(set(e) == set(invocation_log._FIELDS) for e in events)
    text = path.read_text()
    assert "toolu" not in text and str(tmp_path) not in text and "Goal" not in text
    impl, rev, _ = events
    assert (impl["issue"], impl["host"], impl["role"], impl["status"]) == (
        "PLP-5", "claude", "implementer", "routed")
    assert impl["model_requested"] and impl["effort"] and impl["tier"] and impl["at"]
    assert impl["head"] is None and rev["head"] == head and rev["base"] is None
    observe_agent.observe({"hook_event_name": "PostToolUse", "tool_use_id": "toolu_secret_1",
                           "cwd": str(tmp_path)}, environ={})
    observe_agent.observe({"hook_event_name": "PostToolUse", "tool_use_id": "unrelated",
                           "cwd": str(tmp_path)}, environ={})
    events, _ = _lines(tmp_path)
    assert len(events) == 4 and events[3]["event"] == "completed"
    assert events[3]["status"] == "completed" and events[3]["failure_class"] == "none"


def test_background_launch_is_never_recorded_completed(tmp_path):
    """PLP-329: PostToolUse fires at launch for an async Agent; sync stays `completed`."""
    _repo(tmp_path)
    _claude(tmp_path, "implementer", "PLP-5", "toolu_bg_1")
    _claude(tmp_path, "implementer", "PLP-5", "toolu_bg_2")
    _claude(tmp_path, "implementer", "PLP-5", "toolu_sync")
    for tid, response in (("toolu_bg_1", {"isAsync": True, "status": "async_launched"}),
                          ("toolu_bg_2", {"status": "async_launched"}),
                          ("toolu_sync", {"status": "completed", "content": []})):
        observe_agent.observe({"hook_event_name": "PostToolUse", "tool_use_id": tid,
                               "cwd": str(tmp_path), "tool_response": response}, environ={})
    events, _ = _lines(tmp_path)
    done = [(e["status"], e["failure_class"]) for e in events if e["event"] == "completed"]
    assert done == [("unknown", "unknown"), ("unknown", "unknown"), ("completed", "none")]


def test_no_issue_scout_and_failures_record_nothing_and_never_raise(tmp_path, monkeypatch):
    _repo(tmp_path)
    out = route_agent.route_tool_input(
        {"subagent_type": f"foundry:{AGENT_IDENTITIES['implementer']}", "prompt": _packet()},
        cwd=tmp_path, environ={}, correlation="t")
    assert out is not None
    path = invocation_log._path(invocation_log._repository(tmp_path), None)
    assert not path.exists()
    route = SimpleNamespace(role="scout", host="claude", model="m", effort="low",
                            selected_tier="economy")
    assert invocation_log.record_routed(route, issue_id="PLP-1", invocation_id="a" * 16,
                                        root=tmp_path) is False
    route.role = "implementer"
    monkeypatch.setattr(invocation_log, "_append", lambda *a: (_ for _ in ()).throw(OSError()))
    assert invocation_log.record_routed(route, issue_id="PLP-1", invocation_id="a" * 16,
                                        root=tmp_path) is False


def test_codex_implementer_is_recorded_with_null_unobserved_values(tmp_path):
    _repo(tmp_path)
    plan = codex_spawn_plan("implementer", _packet(), root=tmp_path, issue_id="PLP-7")
    events, _ = _lines(tmp_path)
    assert len(events) == 1
    e = events[0]
    assert (e["host"], e["role"], e["issue"], e["id"], e["status"]) == (
        "codex", "implementer", "PLP-7", plan["execution_id"], "routed")
    assert e["model_requested"] == plan["spawn"]["model"]
    assert e["effort"] == plan["spawn"]["reasoning_effort"]
    assert e["head"] is None and e["failure_class"] is None
    codex_spawn_plan("implementer", _packet(), root=tmp_path)  # no issue: nothing
    assert len(_lines(tmp_path)[0]) == 1


def _record(root, role, issue, ident, head=None, base=None, correction=False, host="claude"):
    route = SimpleNamespace(role=role, host=host, model="m-" + ident, effort="high",
                            selected_tier="balanced")
    if role == "reviewer":
        import foundry.routing as routing
        orig = routing.git_head
        routing.git_head = lambda *_a, **_k: head
        try:
            assert invocation_log.record_routed(route, issue_id=issue, invocation_id=ident * 16,
                                                root=root, base=base)
        finally:
            routing.git_head = orig
    else:
        assert invocation_log.record_routed(route, issue_id=issue, invocation_id=ident * 16,
                                            root=root, correction=correction)


def test_invocations_for_diff_window_and_null_when_undeterminable(tmp_path):
    _repo(tmp_path)
    h1, h2 = "1" * 40, "2" * 40
    _record(tmp_path, "implementer", "PLP-9", "a")
    _record(tmp_path, "implementer", "PLP-10", "9")  # other issue
    _record(tmp_path, "reviewer", "PLP-9", "b", head=h1, base=BASE)
    _record(tmp_path, "implementer", "PLP-9", "c", correction=True, host="codex")
    _record(tmp_path, "reviewer", "PLP-9", "d", head=h2, base=BASE)
    first = invocation_log.invocations_for_diff("PLP-9", h1, BASE, root=tmp_path)
    assert [i["model_requested"] for i in first["invocations"]] == ["m-" + "a"]
    second = invocation_log.invocations_for_diff("PLP-9", h2, BASE, root=tmp_path)
    assert [(i["model_requested"], i["correction"], i["host"]) for i in second["invocations"]] == [
        ("m-c", True, "codex")]
    assert second["reviewer"]["head"] == h2
    # not determinable -> None, never a guess
    assert invocation_log.invocations_for_diff("PLP-9", "3" * 40, BASE, root=tmp_path) is None
    assert invocation_log.invocations_for_diff("PLP-9", h2, "c" * 40, root=tmp_path) is None
    assert invocation_log.invocations_for_diff("PLP-11", h1, root=tmp_path) is None
    assert invocation_log.invocations_for_diff("bad", h1, root=tmp_path) is None
    assert invocation_log.invocations_for_diff("PLP-9", "short", root=tmp_path) is None
    # reviewed head with no implementer in its window: invocations is null
    _record(tmp_path, "reviewer", "PLP-9", "e", head="4" * 40)
    assert invocation_log.invocations_for_diff(
        "PLP-9", "4" * 40, root=tmp_path)["invocations"] is None


def test_cli_verb_prints_json_or_null(tmp_path):
    _repo(tmp_path)
    cmd = [sys.executable, "-m", "foundry.routing", "review-invocations", "PLP-9", "--head", "1" * 40, "--root", str(tmp_path)]
    import os
    env = {**os.environ, "HOME": str(tmp_path / "home"),
           "PYTHONPATH": str(PLUGIN_ROOT / "tooling")}
    env.pop("FOUNDRY_DATA", None)
    out = subprocess.run(cmd, capture_output=True, text=True, env=env)
    assert out.returncode == 0 and json.loads(out.stdout) is None


def test_no_decision_path_reads_the_log():
    allowed = {"invocation_log.py", "routing_facades.py", "routing.py", "token_attribution.py",
               "review_turns.py", "kpi_export.py", "kpi_sessions.py"}
    offenders = [p.name for p in (PLUGIN_ROOT / "tooling" / "foundry").rglob("*.py")
                 if p.name not in allowed
                 and re.search(r"\binvocation_log\b", p.read_text())]
    assert offenders == []
    # routing_facades only writes; routing.py only exposes the read-only CLI verb
    for name, writers in (("routing_facades.py", {"record_routed"}),):
        used = set(re.findall(r"invocation_log\.(\w+)\(",
                              (PLUGIN_ROOT / "tooling" / "foundry" / name).read_text()))
        assert used == writers
    routing_src = (PLUGIN_ROOT / "tooling" / "foundry" / "routing.py").read_text()
    assert set(re.findall(r"invocation_log\.(\w+)\(", routing_src)) == {"invocations_for_diff"}
    assert "invocation_log" not in (PLUGIN_ROOT / "tooling" / "foundry" / "write.py").read_text()
