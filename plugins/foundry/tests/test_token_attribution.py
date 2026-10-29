"""PLP-317: offline token attribution to issue and role, on synthetic logs of both hosts."""
import json
import re
import subprocess
import sys
from pathlib import Path

from foundry import invocation_log, token_attribution

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO = "synthetic-repository"
TOOL_USE = "toolu_SYNTHETIC1"
EXEC_ID = "0123456789abcdef"


def _tokens(n=1):
    return {"input_tokens": n, "cache_read_input_tokens": 10 * n,
            "cache_creation_input_tokens": 100 * n, "output_tokens": 1000 * n}


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _claude_request(at, n=1, session="s-main", suffix="1"):
    return {"type": "assistant", "timestamp": at, "sessionId": session, "requestId": "r" + suffix + at,
            "message": {"id": "m" + suffix + at, "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
                        "usage": _tokens(n)}}


def _claude_call(at, command):
    # a real tool call sits in an assistant message that carries its own (here empty) usage
    return {"type": "assistant", "timestamp": at, "sessionId": "s-main", "requestId": "c" + at,
            "message": {"id": "c" + at, "model": "claude-sonnet-5-5", "stop_reason": "tool_use",
                        "usage": _tokens(0),
                        "content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}


def _cli(verb, issue):
    return f"python3 plugins/foundry/tooling/foundry_cli.py issue {verb} {issue}"


def _seed(state, role, issue, ident, host):
    path = invocation_log._path(REPO, state)
    invocation_log._append(path, {"event": "routed", "id": ident, "issue": issue, "host": host,
                                  "role": role, "at": "2026-10-01T00:00:00.000000Z", "status": "routed"})


def _claude_tree(tmp_path, main_rows, subagents):
    main = _write(tmp_path / "proj" / "s-main.jsonl", main_rows)
    for name, rows, meta, header in subagents:
        sub = tmp_path / "proj" / "s-main" / "subagents" / f"agent-{name}.jsonl"
        first = {"type": "user", "timestamp": rows[0]["timestamp"],
                 "message": {"content": header}}
        _write(sub, [first, *rows])
        if meta is not None:
            sub.with_name(f"agent-{name}.meta.json").write_text(json.dumps(meta))
    return main


def _by(result, **match):
    return [s for s in result["slices"] if all(s[k] == v for k, v in match.items())]


def _tok(slices):
    return sum(s["tokens"]["output_tokens"] for s in slices)


HEADER = "FOUNDRY_ROUTED_AGENT_V1\nResolved role: reviewer\nSECRET-PROMPT-TEXT"


def test_claude_exact_window_approximate_overlap_and_unattributed(tmp_path):
    state = tmp_path / "state"
    _seed(state, "implementer", "PLP-1", invocation_log.claude_invocation_id(TOOL_USE), "claude")
    main = _claude_tree(tmp_path, [
        _claude_request("2026-10-01T09:00:00.000Z", 1),                    # before any window
        _claude_call("2026-10-01T10:00:00.000Z", _cli("start", "PLP-1")),
        _claude_request("2026-10-01T10:30:00.000Z", 2),                    # PLP-1 only
        _claude_call("2026-10-01T11:00:00.000Z", _cli("start", "PLP-2")),
        _claude_request("2026-10-01T11:30:00.000Z", 3),                    # PLP-1 and PLP-2
        _claude_call("2026-10-01T12:00:00.000Z", _cli("merge", "PLP-1") + " 12"),
        _claude_request("2026-10-01T12:30:00.000Z", 4),                    # PLP-2 window unclosed
    ], [
        ("exact", [_claude_request("2026-10-01T10:40:00.000Z", 5, "sub1", "a")],
         {"toolUseId": TOOL_USE}, "plain text"),                            # record, no header role
        ("hist", [_claude_request("2026-10-01T10:50:00.000Z", 6, "sub2", "b")],
         {"toolUseId": "toolu_NOTRECORDED"}, HEADER),                       # approximate, header role
        ("none", [_claude_request("2026-10-01T08:00:00.000Z", 7, "sub3", "c")],
         None, [{"type": "text", "text": "free"}]),                         # no link at all
    ])
    result = token_attribution.report(claude_sessions=[main], repository=REPO, state_dir=state)
    assert result["invocation_log"] == "read" and result["unreadable_logs"] == {"coordinator": 0, "subagent": 0}
    exact = _by(result, method="exact")
    assert [(s["issue"], s["role"], s["role_source"], s["scope"]) for s in exact] == [
        ("PLP-1", "implementer", "invocation_record", "subagent")]
    assert _tok(exact) == 5000
    window = _by(result, method="window")
    assert [(s["issue"], _tok([s])) for s in window] == [("PLP-1", 2000)]
    approx = _by(result, method="approximate")
    assert [(s["issue"], s["role"], s["role_source"], _tok([s])) for s in approx] == [
        ("PLP-1", "reviewer", "packet_header", 6000)]
    overlap = _by(result, method="overlap")
    assert [(s["issues"], s["issue"], _tok([s])) for s in overlap] == [(["PLP-1", "PLP-2"], None, 3000)]
    reasons = {(s["scope"], s["reason"]): _tok([s]) for s in _by(result, method="unattributed")}
    assert reasons == {("coordinator", "no_window"): 1000, ("coordinator", "window_unclosed"): 4000,
                       ("subagent", "no_window"): 7000}
    assert [s for s in _by(result, method="unattributed", scope="subagent")][0]["role"] is None
    # nothing is lost nor divided: the slices add up to the reader's own total
    assert result["totals"]["tokens"]["output_tokens"] == 1000 * (1 + 2 + 3 + 4 + 5 + 6 + 7)
    assert result["totals"]["requests"] == 7 + 3  # three empty tool-call messages
    windows = {w["issue"]: w for w in result["windows"]}
    assert windows["PLP-1"]["closed"] is True
    assert windows["PLP-2"]["closed"] is False and windows["PLP-2"]["end"] is None
    # an unclosed window is treated as still open: it overlaps the closed one, both declare it
    assert windows["PLP-1"]["overlaps_with"] == ["PLP-2"] and windows["PLP-2"]["overlaps_with"] == ["PLP-1"]


def test_overlap_of_two_closed_windows_is_declared_on_both(tmp_path):
    main = _claude_tree(tmp_path, [
        _claude_call("2026-10-01T10:00:00.000Z", _cli("start", "PLP-1")),
        _claude_call("2026-10-01T10:10:00.000Z", _cli("start", "PLP-2")),
        _claude_request("2026-10-01T10:20:00.000Z", 1),
        _claude_call("2026-10-01T10:30:00.000Z", _cli("merge", "PLP-1")),
        _claude_call("2026-10-01T10:40:00.000Z", _cli("merge", "PLP-2")),
        _claude_request("2026-10-01T10:50:00.000Z", 2),
    ], [])
    result = token_attribution.report(claude_sessions=[main], repository=REPO, state_dir=tmp_path / "s")
    assert {w["issue"]: w["overlaps_with"] for w in result["windows"]} == {
        "PLP-1": ["PLP-2"], "PLP-2": ["PLP-1"]}
    assert [(s["issues"], _tok([s])) for s in _by(result, method="overlap")] == [(["PLP-1", "PLP-2"], 1000)]
    assert _tok(_by(result, method="window")) == 0  # only the empty tool-call messages
    assert [(s["reason"], _tok([s])) for s in _by(result, method="unattributed")] == [("no_window", 2000)]


def test_unreadable_record_and_log_are_reported_never_guessed(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n")
    state = tmp_path / "state"
    path = invocation_log._path(REPO, state)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * (invocation_log._MAX_BYTES + 1))
    main = _claude_tree(tmp_path, [_claude_request("2026-10-01T10:00:00.000Z", 1)], [])
    result = token_attribution.report(claude_sessions=[main, bad], repository=REPO, state_dir=state)
    assert result["invocation_log"] == "unreadable" and result["invocation_records"] is None
    assert result["unreadable_logs"]["coordinator"] == 1
    assert [s["method"] for s in result["slices"]] == ["unattributed"]


def _codex_usage(n):
    return {"input_tokens": 11 * n, "cached_input_tokens": 10 * n, "cache_write_input_tokens": 0,
            "output_tokens": 1000 * n, "reasoning_output_tokens": 0}


def _codex_log(path, sid, calls_and_turns, *, parent=None, agent_path=None):
    meta = {"id": sid, "cwd": "/synthetic", "model": "gpt-5"}
    if parent:
        meta.update({"parent_thread_id": parent, "thread_source": "subagent", "agent_path": agent_path})
    rows, total = [{"type": "session_meta", "timestamp": calls_and_turns[0][1], "payload": meta}], 0
    for kind, at, value in calls_and_turns:
        if kind == "turn":
            total += value
            rows.append({"type": "event_msg", "timestamp": at, "payload": {
                "type": "token_count", "info": {"last_token_usage": _codex_usage(value),
                                                "total_token_usage": _codex_usage(total)}}})
        else:
            rows.append({"type": "response_item", "timestamp": at, "payload": {
                "type": "custom_tool_call", "name": "exec",
                "input": 'await tools.exec_command({cmd:"' + value + '"})'}})
    return _write(path, rows)


def test_codex_exact_agent_name_window_and_unlinked_subagent(tmp_path):
    state = tmp_path / "state"
    _seed(state, "reviewer", "PLP-3", EXEC_ID, "codex")
    coordinator = _codex_log(tmp_path / "rollout-a.jsonl", "thread-a", [
        ("call", "2026-10-02T10:00:00.000Z", _cli("start", "PLP-3")),
        ("turn", "2026-10-02T10:10:00.000Z", 1),
        ("turn", "2026-10-02T10:20:00.000Z", 2),
        ("call", "2026-10-02T10:30:00.000Z", _cli("merge", "PLP-3")),
        ("turn", "2026-10-02T10:40:00.000Z", 3),
    ])
    linked = _codex_log(tmp_path / "rollout-b.jsonl", "thread-b", [("turn", "2026-10-02T10:15:00.000Z", 4)],
                        parent="thread-a", agent_path=f"/root/foundry_maigret_{EXEC_ID}")
    named = _codex_log(tmp_path / "rollout-c.jsonl", "thread-c", [("turn", "2026-10-02T10:16:00.000Z", 5)],
                       parent="thread-a", agent_path="/root/foundry_eiffel_fedcba9876543210")
    custom = _codex_log(tmp_path / "rollout-d.jsonl", "thread-d", [("turn", "2026-10-02T10:17:00.000Z", 6)],
                        parent="thread-a", agent_path="/root/other_name_fedcba9876543211")
    orphan = _codex_log(tmp_path / "rollout-e.jsonl", "thread-e", [("turn", "2026-10-02T10:18:00.000Z", 7)],
                        parent="thread-missing", agent_path="/root/foundry_lupin_fedcba9876543212")
    result = token_attribution.report(codex_sessions=[coordinator, linked, named, custom, orphan],
                                      repository=REPO, state_dir=state)
    assert [(s["issue"], s["role"], s["role_source"], _tok([s])) for s in _by(result, method="exact")] == [
        ("PLP-3", "reviewer", "invocation_record", 4000)]
    assert [(s["issue"], _tok([s])) for s in _by(result, method="window")] == [("PLP-3", 3000)]
    approx = {(s["role"], s["role_source"]): _tok([s]) for s in _by(result, method="approximate")}
    assert approx == {("implementer", "agent_name"): 5000, (None, None): 6000}
    unattributed = {(s["role"], s["reason"]): _tok([s]) for s in _by(result, method="unattributed")}
    assert unattributed == {(None, "no_window"): 3000, ("scout", "no_window"): 7000}
    assert result["totals"]["tokens"]["output_tokens"] == 28000


def test_replayable_deterministic_and_content_free(tmp_path):
    state = tmp_path / "state"
    _seed(state, "implementer", "PLP-1", invocation_log.claude_invocation_id(TOOL_USE), "claude")
    main = _claude_tree(tmp_path, [
        _claude_call("2026-10-01T10:00:00.000Z", _cli("start", "PLP-1") + " --secret-flag=hunter2"),
        _claude_request("2026-10-01T10:30:00.000Z", 2),
    ], [("x", [_claude_request("2026-10-01T10:40:00.000Z", 5, "sub1", "a")],
         {"toolUseId": TOOL_USE, "description": "SECRET-DESCRIPTION"}, HEADER)])
    first = token_attribution.report(claude_sessions=[main], repository=REPO, state_dir=state)
    second = token_attribution.report(claude_sessions=[main, main], repository=REPO, state_dir=state)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    text = json.dumps(first)
    for leak in ("SECRET", "hunter2", str(tmp_path), TOOL_USE, "foundry_cli", "s-main", "sub1"):
        assert leak not in text


def test_reader_rows_are_unchanged_without_timestamps(tmp_path):
    from foundry.cost_attribution import read_host_log

    main = _claude_tree(tmp_path, [_claude_request("2026-10-01T10:30:00.000Z", 2)], [])
    plain = read_host_log("claude", main)
    timed = read_host_log("claude", main, timestamps=True)
    assert all("request_at" not in row for row in plain)
    assert [{k: v for k, v in row.items() if k != "request_at"} for row in timed] == plain
    assert timed[0]["request_at"] == ["2026-10-01T10:30:00.000Z"]
    codex = _codex_log(tmp_path / "c.jsonl", "t", [("turn", "2026-10-02T10:10:00.000Z", 1)])
    assert all("request_at" not in row for row in read_host_log("codex", codex))
    assert read_host_log("codex", codex, timestamps=True)[0]["request_at"] == ["2026-10-02T10:10:00.000Z"]


def test_module_has_no_network_tracker_or_write_surface():
    source = (PLUGIN_ROOT / "tooling" / "foundry" / "token_attribution.py").read_text()
    imports = set(re.findall(r"^(?:from|import) ([\w.]+)", source, re.M))
    assert imports <= {"__future__", "json", "re", "datetime", "pathlib", "foundry",
                       "foundry.cost_attribution", "foundry.routing_facades"}
    assert not re.search(r"\b(write_text|write_bytes|urlopen|socket|subprocess|record_routed|_append)\b", source)


def test_cli_verb_prints_sorted_json(tmp_path):
    main = _claude_tree(tmp_path, [_claude_request("2026-10-01T10:30:00.000Z", 2)], [])
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "PYTHONPATH": str(PLUGIN_ROOT / "tooling")}
    out = subprocess.run([sys.executable, "-m", "foundry.routing", "token-attribution", "--session",
                          str(main), "--repository", REPO], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["schema"] == "token-attribution.v1" and data["totals"]["requests"] == 1
    assert out.stdout == json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def test_windows_open_on_the_quoted_skill_form_in_both_hosts(tmp_path):
    quoted = 'python3 "${CLAUDE_PLUGIN_ROOT}/tooling/foundry_cli.py" issue VERB PLP-9'
    main = _claude_tree(tmp_path, [
        _claude_call("2026-10-03T10:00:00.000Z", quoted.replace("VERB", "start")),
        _claude_request("2026-10-03T10:10:00.000Z", 1),
        _claude_call("2026-10-03T10:20:00.000Z", quoted.replace("VERB", "merge") + " 12"),
    ], [])
    result = token_attribution.report(claude_sessions=[main], repository=REPO, state_dir=tmp_path / "state")
    assert [(s["issue"], _tok([s])) for s in _by(result, method="window")] == [("PLP-9", 1000)]

    def call(at, verb):
        return {"type": "response_item", "timestamp": at, "payload": {
            "type": "function_call", "name": "exec_command",
            "arguments": json.dumps({"cmd": quoted.replace("VERB", verb)})}}
    rows = [{"type": "session_meta", "timestamp": "2026-10-03T10:00:00.000Z",
             "payload": {"id": "thread-q", "cwd": "/synthetic", "model": "gpt-5"}},
            call("2026-10-03T10:00:00.000Z", "start"),
            {"type": "event_msg", "timestamp": "2026-10-03T10:10:00.000Z", "payload": {
                "type": "token_count", "info": {"last_token_usage": _codex_usage(1),
                                                "total_token_usage": _codex_usage(1)}}},
            call("2026-10-03T10:20:00.000Z", "merge")]
    assert '\\"' in rows[1]["payload"]["arguments"]
    rollout = _write(tmp_path / "rollout-q.jsonl", rows)
    result = token_attribution.report(codex_sessions=[rollout], repository=REPO, state_dir=tmp_path / "state")
    assert [(s["issue"], _tok([s])) for s in _by(result, method="window")] == [("PLP-9", 1000)]


def test_subagent_of_an_unreadable_coordinator_is_not_called_no_window(tmp_path):
    main = tmp_path / "proj" / "s-main.jsonl"
    main.parent.mkdir(parents=True)
    main.write_text("not json\n")
    sub = tmp_path / "proj" / "s-main" / "subagents" / "agent-a.jsonl"
    _write(sub, [{"type": "user", "timestamp": "2026-10-03T10:00:00.000Z", "message": {"content": HEADER}},
                 _claude_request("2026-10-03T10:05:00.000Z", 1, session="s-sub")])
    result = token_attribution.report(claude_sessions=[main], repository=REPO, state_dir=tmp_path / "state")
    assert result["unreadable_logs"]["coordinator"] == 1
    assert [(s["reason"], s["scope"]) for s in result["slices"]] == [("parent_unreadable", "subagent")]


def test_invalid_utf8_header_line_reads_as_unreadable_not_as_a_failure(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b'{"type": "x"}\n\xff\xfe\n')
    assert token_attribution._packet_role(bad) is None
    assert token_attribution._codex_meta(bad) is None
    result = token_attribution.report(claude_sessions=[bad], codex_sessions=[bad], repository=REPO,
                                      state_dir=tmp_path / "s")
    assert result["unreadable_logs"] == {"coordinator": 2, "subagent": 0}


def test_only_the_cli_verb_imports_token_attribution():
    importers = {p.name for p in (PLUGIN_ROOT / "tooling").rglob("*.py")
                 if re.search(r"^\s*(from foundry import .*\btoken_attribution\b|from foundry\.token_attribution\b|import foundry\.token_attribution)",
                              p.read_text(), re.M)}
    assert importers == {"routing.py", "kpi_export.py", "kpi_sessions.py"}  # the CLI verb, the PLP-319 export and the PLP-342 discovery
