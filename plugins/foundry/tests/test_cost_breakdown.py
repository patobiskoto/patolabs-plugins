"""PAT-129: offline breakdown of premium work (fixtures are synthetic or the committed evidence; no model)."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from foundry import cost_breakdown as cb
from foundry.cost_attribution import PRICE_GRID_PATH, load_price_grid

RUNS = Path(__file__).resolve().parents[1] / "docs" / "qualification" / "pat-19-runs"


def _grid(tmp_path, **patch):
    data = json.loads(cb.GRID_PATH.read_text(encoding="utf-8"))
    data["entries"].extend(patch.get("extra", []))
    path = tmp_path / "grid.json"
    path.write_text(json.dumps(data))
    return path


def _tok(i=0, c=0, w=0, o=0, r=0):
    return {"input_tokens": i, "cached_input_tokens": c, "cache_write_input_tokens": w, "output_tokens": o,
            "reasoning_output_tokens": r}


def _record(arm, pr, roles, models, accepted=None, sessions=("s1",), segment="cloud"):
    return {"record_type": "attempt", "dry_run": False, "path": arm, "segment": segment, "task": {"pr": pr},
            "accepted": accepted, "cloud_sessions": list(sessions),
            "premium": {"billing_total": 1, "by_role": roles, "by_model": models}}


def _unpriced(models=(), unresolved=0, candidates=(), no_day=0):
    return {"status": "unavailable", "models_without_price": list(models),
            "unresolved_model_cells": {"count": unresolved, "candidate_models": list(candidates)},
            "cells_without_session_day": no_day}


SESSIONS = {"s1": {"role": "implementer", "day": "2026-10-07"}, "s2": {"role": "reviewer", "day": "2026-10-07"}}


# --- committed evidence: totals equal those of the committed reports ---------------------------------

@pytest.mark.parametrize("name", ["x4compare-1", "x5compare-1"])
def test_totals_equal_the_committed_report(name):
    doc = cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", report=RUNS / name / f"report-pat-19-{name}.json")
    check = doc["totals_equal_committed_report"]
    assert check["all_equal"], check
    for arm in ("A", "L"):
        by_role = doc["arms"][arm]["by_role"]
        assert sum(v["tokens"]["billing_total"] for v in by_role.values()) == doc["arms"][arm]["tokens"]["billing_total"]
        assert by_role["explorer"]["tokens"]["billing_total"] == 0
        assert doc["arms"][arm]["weighted"]["status"] == "priced"
        low = doc["arms"][arm]["weighted"]["total_usd"]
        assert low["cache_write_all_5m"] < low["cache_write_all_1h"]


def test_v5_paired_reading_is_outside_the_rule_and_agrees_with_the_frozen_ratio():
    name = "x5compare-1"
    doc = cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", report=RUNS / name / f"report-pat-19-{name}.json")
    paired = doc["paired_reading"]
    assert paired["outside_the_frozen_rule"] is True
    assert paired["unweighted_ratio_L_over_A_per_accepted"] == paired["frozen_ratio_in_report"] == 0.8579
    assert paired["arms"]["A"]["billing_tokens"] == 9178695 and paired["arms"]["L"]["billing_tokens"] == 7874314
    committed = json.loads((RUNS / name / f"report-pat-19-{name}.json").read_text())
    accepted = committed["exploration_comparison"]["paired_rule"]["accepted_on_paired"]
    assert (paired["arms"]["A"]["accepted"], paired["arms"]["L"]["accepted"]) == (accepted["A"], accepted["L"])
    assert paired["accepted_counts_equal_records"] is True
    assert set(paired["weighted_ratio_L_over_A_per_accepted"]) == {"cache_write_all_5m", "cache_write_all_1h"}


def test_v4_report_has_no_paired_rule_so_no_paired_reading():
    name = "x4compare-1"
    doc = cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", report=RUNS / name / f"report-pat-19-{name}.json")
    assert "paired_reading" not in doc


def _paired_fixture(accepted_a, accepted_l, flags=(True, False)):
    records = [_record("A", 1, {"implementer": _tok(c=1_000_000)}, {"claude-sonnet-5-5": _tok(c=1_000_000)}, accepted=flags[0]),
               _record("L", 1, {"implementer": _tok(c=500_000)}, {"claude-sonnet-5-5": _tok(c=500_000)}, accepted=flags[1])]
    report = {"exploration_comparison": {"paired_rule": {"paired_decided": [1], "ratio": None,
                                                         "accepted_on_paired": {"A": accepted_a, "L": accepted_l}}}}
    result = cb.breakdown(records, SESSIONS, cb.load_grid())
    return cb.paired_reading(result, report, cb.load_grid())


def test_ratio_is_unavailable_when_an_arm_accepts_zero():
    paired = _paired_fixture(1, 0)
    assert paired["weighted_ratio_L_over_A_per_accepted"] == {"status": "unavailable"}
    assert paired["unweighted_ratio_L_over_A_per_accepted"] is None
    assert paired["accepted_counts_equal_records"] is True


def test_the_committed_accepted_count_decides_and_a_difference_with_the_records_is_flagged():
    paired = _paired_fixture(1, 1)  # the report says L accepted one task, the records say none
    assert paired["arms"]["L"]["accepted"] == 1
    assert paired["accepted_counts_equal_records"] is False
    assert set(paired["weighted_ratio_L_over_A_per_accepted"]) == {"cache_write_all_5m", "cache_write_all_1h"}


def test_the_analysis_does_not_recompute_a_verdict():
    name = "x5compare-1"
    text = json.dumps(cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", report=RUNS / name / f"report-pat-19-{name}.json"))
    assert "keep_cloud" not in text and "verdict" not in text


# --- price grid ----------------------------------------------------------------------------------------

def test_haiku_4_5_stays_priced_only_by_the_existing_grid():
    """FOUNDRY-ADR-0015 refuses overlapping windows for a host/model: the new grid does not repeat Haiku 4.5."""
    assert cb.price_for(cb.load_grid(), "claude", "claude-haiku-4-5-20251001", "2026-10-07") is None
    assert cb.price_for(cb.load_grid(), "claude", "claude-haiku-4-5", "2026-10-07") is None
    assert any(e["model"] == "haiku-4.5" for e in load_price_grid(PRICE_GRID_PATH)["entries"])


def test_existing_pricing_grid_is_untouched_and_has_no_5_5_entry():
    assert cb.price_for(cb.load_grid(), "claude", "claude-sonnet-5-5", "2026-10-07") is not None
    assert not any("5-5" in e["model"] or "5.5" in e["model"] for e in load_price_grid(PRICE_GRID_PATH)["entries"])


def test_grid_is_dated_and_a_date_outside_the_window_has_no_price():
    grid = cb.load_grid()
    assert cb.price_for(grid, "claude", "claude-opus-5-5", "2026-10-05") is not None
    assert cb.price_for(grid, "claude", "claude-opus-5-5", "2026-10-04") is None
    assert cb.price_for(grid, "claude", "claude-unknown", "2026-10-07") is None


def test_overlapping_entries_are_refused(tmp_path):
    entry = json.loads(cb.GRID_PATH.read_text())["entries"][0]
    with pytest.raises(cb.BreakdownError, match="ambiguous"):
        cb.load_grid(_grid(tmp_path, extra=[entry]))


def test_malformed_rates_are_refused(tmp_path):
    data = json.loads(cb.GRID_PATH.read_text())
    del data["entries"][0]["rates"]["cache_write_1h"]
    path = tmp_path / "g.json"
    path.write_text(json.dumps(data))
    with pytest.raises(cb.BreakdownError, match="rates"):
        cb.load_grid(path)


def test_weights_are_exact_and_carry_both_cache_write_bounds():
    entry = cb.price_for(cb.load_grid(), "claude", "claude-sonnet-5-5", "2026-10-07")
    w = cb.weigh(_tok(i=1_000_000, c=1_000_000, w=1_000_000, o=1_000_000), entry)
    assert (w["input"], w["cached_input"], w["cache_write_5m"], w["cache_write_1h"], w["output"]) == (
        Decimal(2), Decimal("0.10"), Decimal("2.50"), Decimal(4), Decimal(10))


def test_a_model_without_a_price_stays_unavailable():
    records = [_record("A", 1, {"implementer": _tok(c=10)}, {"claude-mystery-9": _tok(c=10)})]
    arm = cb.breakdown(records, SESSIONS, cb.load_grid())["arms"]["A"]
    assert arm["weighted"] == _unpriced(["claude-mystery-9"])
    assert arm["tokens"]["billing_total"] == 10  # the unweighted reading is unaffected


def test_haiku_5_5_whose_price_depends_on_prompt_length_is_unavailable():
    records = [_record("A", 1, {"implementer": _tok(c=10)}, {"claude-haiku-5-5": _tok(c=10)})]
    assert cb.breakdown(records, SESSIONS, cb.load_grid())["arms"]["A"]["weighted"]["status"] == "unavailable"


def test_a_day_before_the_window_is_unavailable_not_estimated():
    records = [_record("A", 1, {"implementer": _tok(c=10)}, {"claude-sonnet-5-5": _tok(c=10)})]
    early = {"s1": {"role": "implementer", "day": "2026-09-01"}}
    assert cb.breakdown(records, early, cb.load_grid())["arms"]["A"]["weighted"]["status"] == "unavailable"


# --- synthetic breakdown ----------------------------------------------------------------------------

def test_split_by_role_class_and_model_with_two_models():
    records = [_record("A", 1, {"implementer": _tok(1, 100, 10, 5, 2), "reviewer": _tok(2, 200, 20, 6, 3)},
                       {"claude-sonnet-5-5": _tok(1, 100, 10, 5, 2), "claude-opus-5-5": _tok(2, 200, 20, 6, 3)},
                       accepted=True, sessions=("s1", "s2"))]
    arm = cb.breakdown(records, SESSIONS, cb.load_grid())["arms"]["A"]
    assert arm["by_role"]["implementer"]["tokens"]["billing_total"] == 116
    assert arm["by_role"]["reviewer"]["tokens"]["billing_total"] == 228
    assert arm["by_class"]["cache_read"]["tokens"] == 300
    assert arm["by_class"]["reasoning_output_visible"]["tokens"] == 5
    assert arm["by_model"]["claude-opus-5-5"]["tokens"]["billing_total"] == 228
    opus = arm["by_model"]["claude-opus-5-5"]["weighted"]["total_usd"]["cache_write_all_1h"]
    assert opus == pytest.approx((2 * 4 + 200 * 0.2 + 20 * 8 + 6 * 20) / 1e6, abs=1e-6)


def test_role_to_model_assignment_is_never_guessed():
    same = _tok(0, 5, 0, 1)
    roles = {"implementer": same, "corrector": same}
    models = {"claude-sonnet-5-5": same, "claude-opus-5-5": same}
    assert cb.role_models(roles, models) is None  # two assignments fit: unavailable
    assert cb.role_models({"implementer": _tok(c=1), "reviewer": _tok(c=2)},
                          {"a": _tok(c=1), "b": _tok(c=2)}) == {"implementer": "a", "reviewer": "b"}


def test_an_absent_token_class_is_refused_not_zero():
    bad = _record("A", 1, {"implementer": {"input_tokens": 1}}, {"claude-sonnet-5-5": _tok()})
    with pytest.raises(cb.BreakdownError, match="never zero"):
        cb.breakdown([bad], SESSIONS, cb.load_grid())


def test_dry_run_records_are_not_counted():
    rec = _record("A", 1, {"implementer": _tok(c=10)}, {"claude-sonnet-5-5": _tok(c=10)})
    dry = dict(rec, dry_run=True)
    assert cb.breakdown([rec, dry], SESSIONS, cb.load_grid())["arms"]["A"]["tokens"]["billing_total"] == 10


# --- classification of tool calls -----------------------------------------------------------------------

@pytest.mark.parametrize("name,tool_input,expected", [
    ("Read", {"file_path": "x"}, "explore_read"),
    ("Grep", {"pattern": "x"}, "explore_search"),
    ("Glob", {"pattern": "*"}, "explore_search"),
    ("Edit", {}, "edit"), ("Write", {}, "edit"), ("Agent", {}, "other"), ("bash", {"command": "ls"}, "other"),
    ("Bash", {"command": "cat a.py"}, "explore_bash"),
    ("Bash", {"command": "sed -n '1,40p' a.py"}, "explore_bash"),
    ("Bash", {"command": "head -20 a.py && tail -5 b.py"}, "explore_bash"),
    ("Bash", {"command": "grep -rn 'a|b' src | head -5"}, "explore_bash"),
    ("Bash", {"command": "rg foo tests; echo ---; ls -la"}, "explore_bash"),
    ("Bash", {"command": "cd work && ls 2>&1"}, "explore_bash"),
    ("Bash", {"command": "find . -name '*.py' | wc -l"}, "explore_bash"),
    ("Bash", {"command": "cat a.py 2>/dev/null"}, "explore_bash"),
    ("Bash", {"command": "A=1 cat a.py"}, "explore_bash"),
    ("Bash", {"command": "sed -i 's/a/b/' a.py"}, "bash_other"),
    ("Bash", {"command": "sed 's/a/b/' a.py"}, "bash_other"),
    ("Bash", {"command": "find . -name x -delete"}, "bash_other"),
    ("Bash", {"command": "find . -exec rm {} ;"}, "bash_other"),
    ("Bash", {"command": "cat a.py > b.py"}, "bash_other"),
    ("Bash", {"command": "cat a.py >> b.py"}, "bash_other"),
    ("Bash", {"command": "cat <<EOF\nx\nEOF"}, "bash_other"),
    ("Bash", {"command": "cat $(ls)"}, "bash_other"),
    ("Bash", {"command": "cat `ls`"}, "bash_other"),
    ("Bash", {"command": "python3 -m pytest tests"}, "bash_other"),
    ("Bash", {"command": "ls && pytest"}, "bash_other"),
    ("Bash", {"command": "echo hi"}, "bash_other"),
    ("Bash", {"command": "cat 'unbalanced"}, "bash_other"),
    ("Bash", {"command": ""}, "bash_other"),
    ("Bash", {}, "bash_other"),
])
def test_classification(name, tool_input, expected):
    assert cb.classify_tool_use(name, tool_input) == expected


# --- exploration over synthetic transcripts -------------------------------------------------------------

def _assistant(mid, blocks, usage):
    return {"type": "assistant", "message": {"id": mid, "model": "claude-sonnet-5-5", "content": blocks, "usage": usage}}


def _tool(tid, name, **tool_input):
    return {"type": "tool_use", "id": tid, "name": name, "input": tool_input}


def _usage(i, c, w, o):
    return {"input_tokens": i, "cache_read_input_tokens": c, "cache_creation_input_tokens": w, "output_tokens": o}


def _write(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _session_fixture(tmp_path, with_log):
    final = {"type": "result", "total_cost_usd": 0.5,
             "usage": {**_usage(6, 300, 30, 400), "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                                     "ephemeral_1h_input_tokens": 30}}}
    stream_calls = [
        _assistant("m1", [_tool("t1", "Read", file_path="a")], _usage(2, 100, 20, 5)),
        _assistant("m1", [_tool("t1", "Read", file_path="a")], _usage(2, 100, 20, 5)),  # duplicated event
        _assistant("m2", [{"type": "thinking"}, _tool("t2", "Bash", command="pytest")], _usage(2, 110, 5, 3)),
        _assistant("m3", [{"type": "text"}], _usage(2, 90, 5, 20)),
    ]
    streams = tmp_path / "streams"
    _write(streams / "camp-s1.jsonl", [*stream_calls, final])
    logs = tmp_path / "logs"
    if with_log:
        exact = [_assistant("m1", [_tool("t1", "Read", file_path="a")], _usage(2, 100, 20, 100)),
                 _assistant("m2", [_tool("t2", "Bash", command="pytest")], _usage(2, 110, 5, 200)),
                 _assistant("m3", [{"type": "text"}], _usage(2, 90, 5, 100))]
        _write(logs / "proj" / "s1.jsonl", exact)
    return streams, logs


def test_exploration_counts_and_tokens_with_exact_per_call_output(tmp_path):
    streams, logs = _session_fixture(tmp_path, True)
    records = [_record("A", 1, {"implementer": _tok(6, 300, 30, 400)}, {"claude-sonnet-5-5": _tok(6, 300, 30, 400)})]
    out = cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp", logs)["A"]["implementer"]
    assert out["tool_calls"]["explore_read"] == 1 and out["tool_calls"]["bash_other"] == 1
    assert out["tool_calls"]["exploration"] == 1 and out["tool_calls"]["total"] == 2
    assert out["tool_calls"]["exploration_share"] == 0.5
    assert out["api_calls"] == 3 and out["api_calls_by_kind"] == {"explore_only": 1, "action": 1, "no_tool": 1}
    assert out["explore_only_calls"]["tokens"] == {"input_tokens": 2, "cached_input_tokens": 100,
                                                   "cache_write_input_tokens": 20, "output_tokens": 100}
    assert out["explore_only_calls"]["of_which_first_call_of_session"]["cache_write_input_tokens"] == 20
    assert out["explore_only_calls"]["output_unattributed_slack_tokens"] == 0
    assert out["sessions_with_exact_per_call_output"] == 1 and out["sessions_input_side_equal_to_final_result"] == 1
    assert out["cache_write_split_tokens"] == {"ephemeral_5m": 0, "ephemeral_1h": 30}
    assert out["host_list_cost_usd"] == 0.5


def test_without_exact_logs_the_output_is_bounded_not_estimated(tmp_path):
    streams, _ = _session_fixture(tmp_path, False)
    records = [_record("A", 1, {"implementer": _tok(6, 300, 30, 400)}, {"claude-sonnet-5-5": _tok(6, 300, 30, 400)})]
    out = cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp")["A"]["implementer"]
    assert out["sessions_with_exact_per_call_output"] == 0
    assert out["explore_only_calls"]["tokens"]["output_tokens"] == 5  # the stream's own (partial) figure
    assert out["explore_only_calls"]["output_unattributed_slack_tokens"] == 400 - (5 + 3 + 20)
    assert out["explore_only_calls"]["weighted_with_output_slack"]["status"] == "priced"


def test_exploration_can_be_restricted_to_a_task_set(tmp_path):
    streams, logs = _session_fixture(tmp_path, True)
    records = [_record("A", 1, {"implementer": _tok(6, 300, 30, 400)}, {"claude-sonnet-5-5": _tok(6, 300, 30, 400)})]
    assert cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp", logs, [2]) == {}


def test_a_transcript_without_a_result_is_refused(tmp_path):
    streams = tmp_path / "streams"
    _write(streams / "camp-s1.jsonl", [_assistant("m1", [], _usage(1, 1, 1, 1))])
    records = [_record("A", 1, {"implementer": _tok(c=1)}, {"claude-sonnet-5-5": _tok(c=1)})]
    with pytest.raises(cb.BreakdownError, match="result"):
        cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp")


def _only_role(streams, logs, **kw):
    records = [_record("A", 1, {"implementer": _tok(6, 300, 30, 400)}, {"claude-sonnet-5-5": _tok(6, 300, 30, 400)})]
    return cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp", logs, **kw)["A"]["implementer"]


def test_join_uses_permitted_fields_and_a_mismatch_is_unavailable_not_approximated(tmp_path):
    streams, logs = _session_fixture(tmp_path, True)
    log = logs / "proj" / "s1.jsonl"
    log.write_text(log.read_text().replace('"cache_read_input_tokens": 110', '"cache_read_input_tokens": 111'))
    out = _only_role(streams, logs)
    assert out["explore_only_calls"] == {"status": "unavailable", "reasons": {"output_join": 1}}
    assert out["sessions_with_exact_per_call_output"] == 0
    assert out["tool_calls"]["exploration"] == 1  # the counts need no host log


def test_ambiguous_join_keys_are_unavailable(tmp_path):
    streams, logs = _session_fixture(tmp_path, True)
    path = streams / "camp-s1.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events[3]["message"]["usage"]["cache_read_input_tokens"] = 90  # m2 and m3 now carry equal counters
    events[2]["message"]["usage"]["cache_read_input_tokens"] = 90
    events[2]["message"]["usage"]["cache_creation_input_tokens"] = 5
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    assert _only_role(streams, logs)["explore_only_calls"]["status"] == "unavailable"


def test_a_log_entry_with_two_output_values_for_one_key_is_unavailable(tmp_path):
    streams, logs = _session_fixture(tmp_path, True)
    log = logs / "proj" / "s1.jsonl"
    extra = _assistant("mx", [{"type": "text"}], _usage(2, 100, 20, 7))
    log.write_text(log.read_text() + json.dumps(extra) + "\n")
    assert _only_role(streams, logs)["explore_only_calls"]["status"] == "unavailable"


def test_input_side_mismatch_with_the_final_result_makes_the_role_unavailable(tmp_path):
    streams, logs = _session_fixture(tmp_path, True)
    path = streams / "camp-s1.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events[-1]["usage"]["cache_read_input_tokens"] = 301
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    out = _only_role(streams, logs)
    assert out["explore_only_calls"] == {"status": "unavailable", "reasons": {"input_side_mismatch": 1}}


def test_host_log_reader_extracts_only_counters_and_the_model_alias(tmp_path):
    _, logs = _session_fixture(tmp_path, True)
    got = cb.read_host_counters(logs / "proj" / "s1.jsonl")
    assert got == {("claude-sonnet-5-5", 2, 100, 20): {100}, ("claude-sonnet-5-5", 2, 110, 5): {200},
                   ("claude-sonnet-5-5", 2, 90, 5): {100}}


def test_absent_control_fields_stay_unknown(tmp_path):
    streams, logs = _session_fixture(tmp_path, True)
    path = streams / "camp-s1.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    result = events[-1]
    del result["total_cost_usd"]
    del result["usage"]["cache_creation"]
    del result["usage"]["cache_read_input_tokens"]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    records = [_record("A", 1, {"implementer": _tok(6, 300, 30, 400)}, {"claude-sonnet-5-5": _tok(6, 300, 30, 400)})]
    out = cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp", logs)["A"]["implementer"]
    assert out["host_list_cost_usd"] is None
    assert out["cache_write_split_tokens"] == {"ephemeral_5m": None, "ephemeral_1h": None}
    assert out["sessions_input_side_check_unavailable"] == 1 and out["sessions_input_side_equal_to_final_result"] == 0
    # an absent control counter never lets the attribution through as if it had been checked
    assert out["explore_only_calls"] == {"status": "unavailable", "reasons": {"final_counter_absent": 1}}
    assert out["sessions_with_exact_per_call_output"] == 0
    assert out["tool_calls"]["exploration"] == 1  # the counts need no counter


def test_a_session_with_output_but_no_api_call_is_unavailable_not_an_error(tmp_path):
    streams = tmp_path / "streams"
    _write(streams / "camp-s1.jsonl", [{"type": "result", "usage": _usage(0, 0, 0, 7)}])
    records = [_record("A", 1, {"implementer": _tok(o=7)}, {"claude-sonnet-5-5": _tok(o=7)})]
    out = cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp")["A"]["implementer"]
    assert out["explore_only_calls"] == {"status": "unavailable", "reasons": {"output_without_api_call": 1}}
    assert out["api_calls"] == 0


def test_cli_does_not_crash_on_a_session_without_api_call(tmp_path):
    results = tmp_path / "results-camp.jsonl"
    record = dict(_record("A", 1, {"implementer": _tok(o=7)}, {"claude-sonnet-5-5": _tok(o=7)}), campaign_id="camp")
    results.write_text(json.dumps(record) + "\n")
    (tmp_path / "ledger-camp.jsonl").write_text(json.dumps(
        {"kind": "cloud_started", "dry_run": False, "at": 1791392445.0, "session_id": "s1", "role": "implementer"}) + "\n")
    _write(tmp_path / "streams" / "camp-s1.jsonl", [{"type": "result", "usage": _usage(0, 0, 0, 7)}])
    out = tmp_path / "o.json"
    assert cb.main(["--results", str(results), "--streams-dir", str(tmp_path / "streams"), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["exploration"]["A"]["implementer"]["explore_only_calls"]["status"] == "unavailable"


def test_an_absent_per_call_counter_makes_the_role_unavailable(tmp_path):
    streams, _ = _session_fixture(tmp_path, False)
    path = streams / "camp-s1.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    del events[2]["message"]["usage"]["cache_read_input_tokens"]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    records = [_record("A", 1, {"implementer": _tok(6, 300, 30, 400)}, {"claude-sonnet-5-5": _tok(6, 300, 30, 400)})]
    out = cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp")["A"]["implementer"]
    assert out["explore_only_calls"]["status"] == "unavailable"


def test_a_sensitivity_replaces_one_rate_and_leaves_the_main_reading_alone(tmp_path):
    name = "x5compare-1"
    args = dict(report=RUNS / name / f"report-pat-19-{name}.json")
    plain = cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", **args)
    sens = cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", overrides=["claude-sonnet-5-5:cache_read=0.20"], **args)
    assert sens["arms"] == plain["arms"]
    low = sens["sensitivity"]["arms"]["A"]["weighted"]["total_usd"]["cache_write_all_1h"]
    assert low > plain["arms"]["A"]["weighted"]["total_usd"]["cache_write_all_1h"]
    assert sens["sensitivity"]["paired_reading"]["weighted_ratio_L_over_A_per_accepted"]["cache_write_all_1h"] < 1.02
    with pytest.raises(cb.BreakdownError):
        cb.apply_override(cb.load_grid(), "claude-nothing:cache_read=1")
    with pytest.raises(cb.BreakdownError):
        cb.apply_override(cb.load_grid(), "bad")


def test_the_price_basis_is_in_the_output():
    name = "x5compare-1"
    doc = cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl")
    assert all("assumed" in e["effective_from_basis"].lower() or "ASSUMED" in e["effective_from_basis"]
               for e in doc["price_grid"]["entries"])


def test_cli_error_message_never_prints_a_path(tmp_path, capsys):
    missing = tmp_path / "secret-operator-dir" / "results-x.jsonl"
    assert cb.main(["--results", str(missing)]) == 2
    err = capsys.readouterr().err
    assert "secret-operator-dir" not in err and str(tmp_path) not in err


# --- CLI ---------------------------------------------------------------------------------------------------

def test_cli_prints_aggregates_only_and_never_overwrites(tmp_path, capsys):
    name = "x5compare-1"
    out = tmp_path / "out.json"
    args = ["--results", str(RUNS / name / f"results-pat-19-{name}.jsonl"),
            "--report", str(RUNS / name / f"report-pat-19-{name}.json"), "--out", str(out)]
    assert cb.main(args) == 0
    text = out.read_text()
    assert "/Users/" not in text and "/private/" not in text and "/tmp" not in text and "session_id" not in text
    assert cb.main(args) == 2  # exists: refused, not overwritten
    assert "FileExistsError" in capsys.readouterr().err


def test_cli_exits_nonzero_when_the_totals_differ_from_the_report(tmp_path):
    name = "x5compare-1"
    report = json.loads((RUNS / name / f"report-pat-19-{name}.json").read_text())
    report["exploration_comparison"]["arms"]["L"]["economy_detail"]["premium_billing_tokens"] += 1
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    assert cb.main(["--results", str(RUNS / name / f"results-pat-19-{name}.jsonl"), "--report", str(path),
                    "--out", str(tmp_path / "o.json")]) == 1


# --- review round 3 ------------------------------------------------------------------------------------

def test_unpriced_models_alone_are_listed():
    records = [_record("A", 1, {"implementer": _tok(c=10), "reviewer": _tok(c=20)},
                       {"claude-sonnet-5-5": _tok(c=10), "claude-mystery-9": _tok(c=20)}, sessions=("s1", "s2"))]
    weighted = cb.breakdown(records, SESSIONS, cb.load_grid())["arms"]["A"]["weighted"]
    assert weighted == _unpriced(["claude-mystery-9"])


def test_grid_uses_the_adr_field_name_captured_at():
    assert all("captured_at" in e and "captured_on" not in e for e in json.loads(cb.GRID_PATH.read_text())["entries"])


def test_a_none_model_mixed_with_strings_does_not_raise():
    cells = [{"model": None, "day": "2026-10-07", "tokens": _tok(c=1), "models": [None, "claude-sonnet-5-5"]},
             {"model": "claude-mystery-9", "day": "2026-10-07", "tokens": _tok(c=1), "models": ["claude-mystery-9"]}]
    got = cb._weighted_group(cells, cb.load_grid())
    # the unresolved cell is reported under its own key: a priced candidate is never listed as unpriced, no "None"
    assert got == _unpriced(["claude-mystery-9"], unresolved=1, candidates=["claude-sonnet-5-5"])


def test_an_unresolved_cell_is_not_reported_as_a_model_without_price():
    same = _tok(0, 5, 0, 1)
    records = [_record("A", 1, {"implementer": same, "corrector": same},
                       {"claude-sonnet-5-5": same, "claude-opus-5-5": same})]
    arm = cb.breakdown(records, SESSIONS, cb.load_grid())["arms"]["A"]
    expected = _unpriced(unresolved=2, candidates=["claude-opus-5-5", "claude-sonnet-5-5"])
    assert arm["weighted"] == expected
    assert arm["by_model"]["claude-sonnet-5-5"]["weighted"] == expected


def test_a_cell_without_a_session_day_is_not_reported_as_a_model_without_price():
    records = [_record("A", 1, {"implementer": _tok(c=10)}, {"claude-sonnet-5-5": _tok(c=10)}, sessions=())]
    assert cb.breakdown(records, SESSIONS, cb.load_grid())["arms"]["A"]["weighted"] == _unpriced(no_day=1)


@pytest.mark.parametrize("command", [
    "sort -o out.txt a.txt", "sort --output=out.txt a.txt", "sort -ro out a", "uniq in.txt out.txt",
    "cat a | tee out.txt", "cp a b", "mv a b", "touch a", "mkdir d", "rm a", "git commit -am x", "git checkout a",
    "sed -n '1,5w out.txt' a.py", "sed -n 's/a/b/w out.txt' a.py", "sed -i -n p a", "cat a >| out", "ls &> out",
    "find . -fprint out.txt", "cat a > /tmp/x", "echo hi >> a.txt", "cat a | python3 -c 'pass'",
    # round 4: composite punctuation is unreadable, the null device must be exactly the null device
    "ls &>> out", "ls;>out", "ls ;> out", "ls >>> out", "ls ;; cat a", "ls &&& cat a", "ls ||| cat a", "ls;&cat a",
    "cat a > /dev/nullx", "cat a >/dev/null/x", "cat a 2>> /dev/null.log", "cat a &>/dev/null2", "(ls)", "cat a <b",
    "cat ''",
    # round 4: options of read commands that run a script or a program
    "sed -n -f script.sed a.py", "sed -nf script.sed a.py", "sed -n --file=script.sed a.py", "sed -n --file script.sed a",
    "sed -n -e's/a/b/w out.txt' a.py", "sed -n --expression='1,5w out.txt' a.py", "sed -n -e'1,5w out.txt' a.py",
    "rg --pre cat foo", "rg --pre=./run.sh foo .", "rg foo --pre-glob '*.pdf' --pre ./run.sh", "cat a | rg --pre x y",
    "cat a | sort --compress-program=./run.sh",
])
def test_writers_are_never_exploration(command):
    assert cb.classify_tool_use("Bash", {"command": command}) == "bash_other"


@pytest.mark.parametrize("command", [
    "cat a.txt | sort | uniq -c", "cat a.txt | uniq", "sed -n 's/a/word/p' a.py", "sed -n '1,5p' a.py", "cat a | sort -u | wc -l",
    # round 4: the neutral forms stay readable
    "ls &&\ncat a", "ls\n\ncat a", "ls;\ncat a", "ls |\n wc -l", "cat a > /dev/null", "cat a 2>/dev/null; ls",
    "cat a >/dev/null 2>&1", "ls &>/dev/null && cat a", "cat a 2>/dev/null|wc -l",
    "rg -n --pretty foo .", "sed -n -e '1,5p' a.py", "sed -n -e'1,5p' a.py",
])
def test_readers_that_look_like_writers_stay_exploration(command):
    assert cb.classify_tool_use("Bash", {"command": command}) == "explore_bash"


def test_accepted_count_disagreement_exits_one_and_still_writes_the_file(tmp_path):
    name = "x5compare-1"
    report = json.loads((RUNS / name / f"report-pat-19-{name}.json").read_text())
    report["exploration_comparison"]["paired_rule"]["accepted_on_paired"]["L"] = 4
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    out = tmp_path / "o.json"
    assert cb.main(["--results", str(RUNS / name / f"results-pat-19-{name}.jsonl"), "--report", str(path),
                    "--out", str(out)]) == 1
    assert json.loads(out.read_text())["paired_reading"]["accepted_counts_equal_records"] is False


# --- review round 4: arm totals only compare on the tasks where both arms spent premium --------------------

def test_task_coverage_names_the_tasks_one_arm_spent_nothing_on_and_compares_on_the_common_set():
    def sonnet(n):
        return {"implementer": _tok(c=n)}, {"claude-sonnet-5-5": _tok(c=n)}

    records = [_record("A", 1, *sonnet(1_000_000)), _record("A", 2, *sonnet(3_000_000)),
               _record("L", 1, *sonnet(500_000)),
               _record("L", 2, {}, {}, sessions=(), segment="explore")]  # contaminated: no cloud session followed
    grid = cb.load_grid()
    cover = cb.task_coverage(cb.breakdown(records, SESSIONS, grid), grid)
    assert cover["outside_the_frozen_rule"] is True and cover["arm_totals_cover_the_same_tasks"] is False
    assert cover["tasks_with_premium_in_every_arm"] == [1]
    assert cover["arms"]["L"]["tasks_with_a_record"] == 2 and cover["arms"]["L"]["tasks_with_premium"] == 1
    assert cover["arms"]["L"]["tasks_without_premium"] == [2] and cover["arms"]["A"]["tasks_without_premium"] == []
    assert cover["arms"]["A"]["on_common_tasks"]["billing_tokens"] == 1_000_000  # not the 4 000 000 of the arm
    ratio = cover["ratio_L_over_A_of_totals_on_common_tasks"]
    assert ratio == {"unweighted": 0.5, "weighted": {"cache_write_all_5m": 0.5, "cache_write_all_1h": 0.5}}  # not 0.125


def test_task_coverage_on_the_committed_campaigns():
    """v4: L spent nothing on PR 48 (contaminated exploration); v5: nothing on PR 33 and 42. Neither total compares."""
    expected = {"x4compare-1": ([48], 5, 3274632, 2870126), "x5compare-1": ([33, 42], 10, 9178695, 7874314)}
    for name, (missing, common, a_tokens, l_tokens) in expected.items():
        doc = cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", report=RUNS / name / f"report-pat-19-{name}.json")
        cover = doc["task_coverage"]
        assert cover["arm_totals_cover_the_same_tasks"] is False
        assert cover["arms"]["L"]["tasks_without_premium"] == missing and cover["arms"]["A"]["tasks_without_premium"] == []
        assert len(cover["tasks_with_premium_in_every_arm"]) == common
        assert cover["arms"]["A"]["on_common_tasks"]["billing_tokens"] == a_tokens
        assert cover["arms"]["L"]["on_common_tasks"]["billing_tokens"] == l_tokens
        assert cover["arms"]["L"]["on_common_tasks"]["billing_tokens"] == doc["arms"]["L"]["tokens"]["billing_total"]
        assert cover["arms"]["A"]["on_common_tasks"]["billing_tokens"] < doc["arms"]["A"]["tokens"]["billing_total"]
    # v5: the common set is the paired set of the committed report
    assert cover["tasks_with_premium_in_every_arm"] == doc["paired_reading"]["paired_tasks"]


def test_exploration_is_also_given_on_the_common_tasks_when_the_arms_differ(tmp_path):
    streams, _ = _session_fixture(tmp_path, False)
    for copy in ("s2", "s3"):
        (streams / f"camp-{copy}.jsonl").write_text((streams / "camp-s1.jsonl").read_text())
    tokens = ({"implementer": _tok(6, 300, 30, 400)}, {"claude-sonnet-5-5": _tok(6, 300, 30, 400)})
    records = [dict(_record("A", 1, *tokens), campaign_id="camp"),
               dict(_record("A", 2, *tokens, sessions=("s2",)), campaign_id="camp"),
               dict(_record("L", 2, *tokens, sessions=("s3",)), campaign_id="camp")]
    results = tmp_path / "results-camp.jsonl"
    results.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    (tmp_path / "ledger-camp.jsonl").write_text("\n".join(json.dumps(
        {"kind": "cloud_started", "dry_run": False, "at": 1791392445.0, "session_id": s, "role": "implementer"})
        for s in ("s1", "s2", "s3")) + "\n")
    doc = cb.analyse(results, streams_dir=streams)
    assert doc["exploration"]["A"]["implementer"]["sessions"] == 2
    assert doc["exploration_on_common_tasks"]["A"]["implementer"]["sessions"] == 1
    assert doc["exploration_on_common_tasks"]["L"]["implementer"]["sessions"] == 1
