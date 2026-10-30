"""PLP-345: the last request of a subagent log with no terminal record takes its counters from the parent."""
import json

import pytest

from foundry.cost_attribution import ParentLink, read_host_log, read_parent_results

CALL = "toolu_SYNTHETIC1"
AGENT = "agent-synthetic-1"
USAGE = {"input_tokens": 5, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 500,
         "output_tokens": 9000}


def _request(n, *, terminal, usage=None, agent=AGENT):
    base = dict(USAGE) if usage is None else usage
    return {"type": "assistant", "timestamp": f"2026-10-01T10:0{n}:00.000Z", "sessionId": "s-sub",
            "agentId": agent, "requestId": f"r{n}",
            "message": {"id": f"m{n}", "model": "claude-sonnet-5-5",
                        "stop_reason": "end_turn" if terminal else None, "usage": base}}


def _partial_usage(output=7):
    return {**USAGE, "output_tokens": output}


def _result(agent=AGENT, call=CALL, usage=None, with_usage=True):
    tool_result = {"agentId": agent, "prompt": "SECRET PROMPT", "content": [{"text": "SECRET ANSWER"}]}
    if with_usage:
        tool_result["usage"] = dict(USAGE) if usage is None else usage
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call,
                                                                    "content": "SECRET"}]},
            "toolUseResult": tool_result}


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _link(tmp_path, results, call=CALL):
    parent = _write(tmp_path / "parent.jsonl", results)
    return ParentLink(read_parent_results(parent), call)


def _read(tmp_path, requests, link):
    sub = _write(tmp_path / "sub.jsonl", requests)
    diagnostics: dict = {}
    return read_host_log("claude", sub, diagnostics=diagnostics, parent=link), diagnostics


def _partial_log():
    return [_request(1, terminal=True), _request(2, terminal=False, usage=_partial_usage())]


def test_linked_and_equal_input_takes_the_parent_counters(tmp_path):
    rows, diagnostics = _read(tmp_path, _partial_log(), _link(tmp_path, [_result()]))
    (row,) = rows
    assert row["tokens"]["output_tokens"] == 9000 + 9000
    assert "lower_bound_requests" not in row and "request_lower_bound" not in row
    assert row["parent_reported_requests"] == 1 and row["request_parent_reported"] == [False, True]
    assert row["provenance"] == "host_reported"
    assert diagnostics["parent_completed_records"] == [{"source": "parent_tool_result", "count": 1}]
    assert "lower_bound_records" not in diagnostics and "parent_refused_records" not in diagnostics


def test_without_a_link_the_plp_344_lower_bound_is_unchanged(tmp_path):
    rows, diagnostics = _read(tmp_path, _partial_log(), None)
    (row,) = rows
    assert row["tokens"]["output_tokens"] == 9000 + 7 and row["lower_bound_requests"] == 1
    assert "parent_reported_requests" not in row and "request_parent_reported" not in row
    assert diagnostics["lower_bound_records"] == [{"reason": "no_terminal_snapshot", "count": 1}]
    assert "parent_refused_records" not in diagnostics


def _assert_refused(tmp_path, link, reason):
    rows, diagnostics = _read(tmp_path, _partial_log(), link)
    (row,) = rows
    assert row["tokens"]["output_tokens"] == 9000 + 7 and row["lower_bound_requests"] == 1
    assert "parent_reported_requests" not in row
    assert diagnostics["parent_refused_records"] == [{"reason": reason, "count": 1}]
    assert diagnostics["lower_bound_records"] == [{"reason": "no_terminal_snapshot", "count": 1}]
    assert "parent_completed_records" not in diagnostics


def test_input_differs_is_refused(tmp_path):
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        other = {**USAGE, key: USAGE[key] + 1}
        _assert_refused(tmp_path, _link(tmp_path, [_result(usage=other)]), "input_differs")


def test_two_results_for_one_agent_are_refused(tmp_path):
    _assert_refused(tmp_path, _link(tmp_path, [_result(), _result(call="toolu_OTHER")]), "ambiguous_parent_result")


def test_no_parent_result_and_no_parent_log_are_refused(tmp_path):
    _assert_refused(tmp_path, _link(tmp_path, [_result(agent="another")]), "no_parent_result")
    _assert_refused(tmp_path, _link(tmp_path, [_result(with_usage=False)]), "no_parent_result")
    _assert_refused(tmp_path, ParentLink(read_parent_results(tmp_path / "missing.jsonl"), CALL), "parent_unreadable")


def test_a_wrong_or_missing_tool_use_id_is_refused(tmp_path):
    _assert_refused(tmp_path, _link(tmp_path, [_result()], call="toolu_OTHER"), "tool_use_mismatch")
    _assert_refused(tmp_path, _link(tmp_path, [_result()], call=None), "no_agent_link")


def test_an_unreadable_parent_usage_or_a_lower_output_is_refused(tmp_path):
    _assert_refused(tmp_path, _link(tmp_path, [_result(usage={"input_tokens": "x"})]), "unreadable_parent_usage")
    _assert_refused(tmp_path, _link(tmp_path, [_result(usage={**USAGE, "output_tokens": 3})]), "output_below_log")


def test_a_malformed_parent_line_with_a_result_makes_the_parent_unreadable(tmp_path):
    parent = tmp_path / "parent.jsonl"
    parent.write_text('{"toolUseResult": \n', encoding="utf-8")
    assert read_parent_results(parent) is None


def test_a_terminal_last_request_is_unchanged_and_no_refusal_is_reported(tmp_path):
    log = [_request(1, terminal=False, usage=_partial_usage()), _request(2, terminal=True)]
    with_parent, diagnostics = _read(tmp_path, log, _link(tmp_path, [_result(usage={**USAGE, "output_tokens": 1})]))
    without, _ = _read(tmp_path, log, None)
    assert with_parent == without
    assert set(diagnostics) == {"completeness", "billable_records", "affected_records", "excluded_records",
                                "lower_bound_records"}


def test_only_the_last_request_is_completed(tmp_path):
    log = [_request(1, terminal=False, usage=_partial_usage(3)), _request(2, terminal=False, usage=_partial_usage())]
    (row,), diagnostics = _read(tmp_path, log, _link(tmp_path, [_result()]))
    assert row["token_requests"][0]["output_tokens"] == 3 and row["token_requests"][1]["output_tokens"] == 9000
    assert row["lower_bound_requests"] == 1 and row["request_lower_bound"] == [True, False]
    assert row["request_parent_reported"] == [False, True]
    assert diagnostics["lower_bound_records"][0]["count"] == 1


@pytest.mark.parametrize("secret", ["SECRET PROMPT", "SECRET ANSWER", "SECRET"])
def test_nothing_but_counters_and_identifiers_is_read_from_the_parent(tmp_path, secret):
    link = _link(tmp_path, [_result()])
    assert secret not in json.dumps(link.results)
    ((call, tokens, _),) = link.results[AGENT]
    assert call == CALL and set(tokens) == {"input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                                            "output_tokens", "reasoning_output_tokens"}
