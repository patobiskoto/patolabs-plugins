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
    assert paired["arms"]["A"]["accepted"] == paired["arms"]["L"]["accepted"] == 5
    assert set(paired["weighted_ratio_L_over_A_per_accepted"]) == {"cache_write_all_5m", "cache_write_all_1h"}


def test_v4_has_no_ratio_when_no_arm_l_task_is_accepted():
    name = "x4compare-1"
    doc = cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", report=RUNS / name / f"report-pat-19-{name}.json")
    assert "paired_reading" not in doc


def test_the_analysis_does_not_recompute_a_verdict():
    name = "x5compare-1"
    text = json.dumps(cb.analyse(RUNS / name / f"results-pat-19-{name}.jsonl", report=RUNS / name / f"report-pat-19-{name}.json"))
    assert "keep_cloud" not in text and "verdict" not in text


# --- price grid ----------------------------------------------------------------------------------------

def test_haiku_4_5_rates_agree_with_the_existing_grid():
    old = next(e for e in load_price_grid(PRICE_GRID_PATH)["entries"] if e["model"] == "haiku-4.5")["rates"]
    new = cb.price_for(cb.load_grid(), "claude", "claude-haiku-4-5-20251001", "2026-10-07")["rates"]
    assert (new["input"], new["cache_read"], new["cache_write_5m"], new["output"]) == (
        Decimal(str(old["input"])), Decimal(str(old["cached_input"])), Decimal(str(old["cache_write_input"])),
        Decimal(str(old["output"])))


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
    assert arm["weighted"] == {"status": "unavailable", "models_without_price": ["claude-mystery-9"]}
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
        _assistant("m2", [{"type": "thinking"}, _tool("t2", "Bash", command="pytest")], _usage(2, 100, 5, 3)),
        _assistant("m3", [{"type": "text"}], _usage(2, 100, 5, 20)),
    ]
    streams = tmp_path / "streams"
    _write(streams / "camp-s1.jsonl", [*stream_calls, final])
    logs = tmp_path / "logs"
    if with_log:
        exact = [_assistant("m1", [_tool("t1", "Read", file_path="a")], _usage(2, 100, 20, 100)),
                 _assistant("m2", [_tool("t2", "Bash", command="pytest")], _usage(2, 100, 5, 200)),
                 _assistant("m3", [{"type": "text"}], _usage(2, 100, 5, 100))]
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
    assert out["explore_only_calls"]["output_unattributed_upper_slack_tokens"] == 0
    assert out["sessions_with_exact_per_call_output"] == 1 and out["sessions_input_side_equal_to_final_result"] == 1
    assert out["cache_write_split_tokens"] == {"ephemeral_5m": 0, "ephemeral_1h": 30}
    assert out["host_list_cost_usd"] == 0.5


def test_without_exact_logs_the_output_is_bounded_not_estimated(tmp_path):
    streams, _ = _session_fixture(tmp_path, False)
    records = [_record("A", 1, {"implementer": _tok(6, 300, 30, 400)}, {"claude-sonnet-5-5": _tok(6, 300, 30, 400)})]
    out = cb.exploration(records, SESSIONS, streams, cb.load_grid(), "camp")["A"]["implementer"]
    assert out["sessions_with_exact_per_call_output"] == 0
    assert out["explore_only_calls"]["tokens"]["output_tokens"] == 5  # the stream's own (partial) figure
    assert out["explore_only_calls"]["output_unattributed_upper_slack_tokens"] == 400 - (5 + 3 + 20)
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
